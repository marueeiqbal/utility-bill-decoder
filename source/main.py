"""Utility Bill Decoder: reads electricity bill images and writes level1.csv and level2.csv.

Usage: python main.py <bills_dir> <level1_template.csv> <level2_template.csv> <output_dir>
"""
import os, re, sys, csv, glob, json, time
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.1-flash-lite")
client = genai.Client(api_key=os.getenv("API_KEY"))

LEVEL1_PROMPT = """
You are reading a Pakistani electricity bill image (K-Electric, LESCO or IESCO).
Extract the details and return ONLY one JSON object, no extra text.

Use exactly this structure and include every field (use null if not printed,
blank, covered by a grey box, or unreadable. Never guess):

{
  "provider": "KE | LESCO | IESCO",
  "tariff": "string exactly as printed, e.g. A1-R",
  "sanctioned_load_kw": number,
  "bill_month": "YYYY-MM",
  "reading_date": "YYYY-MM-DD",
  "issue_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD",
  "previous_reading": number,
  "current_reading": number,
  "units_consumed": number,
  "charges": [{"type": "energy|fixed|fpa|quarterly_adjustment|surcharge|meter_rent|subsidy|other", "amount": number}],
  "total_charges": number,
  "taxes": [{"type": "gst|electricity_duty|income_tax|municipal_tax|other_tax", "amount": number}],
  "total_taxes": number,
  "current_bill": number,
  "arrears": number,
  "payable_within_due_date": number,
  "payable_after_due_date": number
}

RULES:
- Copy values as printed. Do not recalculate, round or correct anything.
- Amounts are plain numbers: remove Rs., commas. 19,964 -> 19964.
- Credits (CR) and subsidies are NEGATIVE. 38209CR -> -38209.
- Dates: 22 Sep 2026 -> 2026-09-22. Month: MAR 2026 -> 2026-03.
- charges: one entry per line in the charges section. Skip blank lines and lines printed as 0.
  Keep separate entries when the same type appears more than once.
  Types: energy = cost of units (Variable Upto..Units, Cost of Electricity, Energy Charges);
  fixed = fixed charges; fpa = FPA or FCA; quarterly_adjustment = QTA / Uniform Quarterly Adjustment;
  surcharge = e.g. Additional Surcharge (PHL), F.C Surcharge; meter_rent = meter/service rent;
  subsidy = subsidy/relief (negative); other = anything else.
- Never include a charge or tax line whose amount is blank or 0.
- taxes: one entry per line in the tax section. Classify by SECTION, not name.
  gst = sales tax / GST; electricity_duty = ED; income_tax; municipal_tax = e.g. MUCT (KMC);
  other_tax = anything else (extra tax, further tax, TV fee).
  If only a combined tax amount is printed, use [] and put the amount in total_taxes.
- total_charges / total_taxes = the printed subtotals, or null if none printed.
- If the charges section has a "Net Electricity Charges" line, use it as total_charges.
- current_bill = printed amount for this month's bill.
- arrears: read the Arrears row. If the value has stray marks next to it (like "39411 / 2"),
  return the main number. Keep the printed sign. Null if blank.
- payable_after_due_date: if several late amounts are shown, use the HIGHEST.
  If it says something like NOT TO BE PAID, use null.
- Only add Off Peak and Peak registers together when the provider is KE (K-Electric).
  For LESCO and IESCO NEVER add registers: use the single register row whose
  (present reading - previous reading) equals units_consumed.
- If the tax section shows only ONE line labelled just "Taxes" (no breakdown),
  return taxes as [] and put that amount in total_taxes.
- NEVER include names, addresses, CNIC, account/reference/consumer/meter numbers.
"""

HISTORY_PROMPT = """
Look at the bill image. Extract the usage/bill HISTORY table (past months).
Return ONLY JSON: {"history": [{"month": "YYYY-MM", "units": number, "bill_amount": number or null}]}
Rules: one entry per month printed. Credits (CR) or negatives stay negative.
If a month's value is unreadable or blank use null. Never guess. No names or IDs.
"""

FIELDS = ["provider", "tariff", "sanctioned_load_kw", "bill_month", "reading_date",
          "issue_date", "due_date", "previous_reading", "current_reading",
          "units_consumed", "charges", "total_charges", "taxes", "total_taxes",
          "current_bill", "arrears", "payable_within_due_date", "payable_after_due_date"]
NUMERIC = ["sanctioned_load_kw", "previous_reading", "current_reading", "units_consumed",
           "total_charges", "total_taxes", "current_bill", "arrears",
           "payable_within_due_date", "payable_after_due_date"]


def load_image(path):
    ext = path.lower().rsplit(".", 1)[-1]
    mime = "image/png" if ext == "png" else "image/jpeg"
    with open(path, "rb") as f:
        return types.Part.from_bytes(data=f.read(), mime_type=mime)


def to_num(v):
    """Turn strings like '54770CR' or '1,234.5' into numbers. Keeps None."""
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    neg = s.upper().endswith("CR") or s.startswith("-")
    digits = re.sub(r"[^0-9.]", "", s)
    if not digits or digits == ".":
        return None
    n = float(digits)
    n = -n if neg else n
    return int(n) if n == int(n) else n


def clean(result):
    """Safety net: fix types, drop zero lines, make sure every field exists."""
    result = dict(result or {})
    for key in ("charges", "taxes"):
        items = []
        for it in (result.get(key) or []):
            if not isinstance(it, dict):
                continue
            amt = to_num(it.get("amount"))
            if amt in (None, 0):
                continue
            items.append({"type": it.get("type", "other"), "amount": amt})
        result[key] = items
    for f in NUMERIC:
        result[f] = to_num(result.get(f))
    for f in FIELDS:
        result.setdefault(f, None)
    return {f: result[f] for f in FIELDS}


def call_model(parts):
    resp = client.models.generate_content(
        model=MODEL_NAME,
        contents=parts,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
    )
    return json.loads(resp.text)


def extract_bill(path):
    return clean(call_model([load_image(path), LEVEL1_PROMPT]))


def extract_history(path):
    data = call_model([load_image(path), HISTORY_PROMPT])
    hist = data.get("history", []) if isinstance(data, dict) else []
    for h in hist:
        h["units"] = to_num(h.get("units"))
        h["bill_amount"] = to_num(h.get("bill_amount"))
    return hist


def compute_stats(bill, history):
    """All maths is done here in Python, not by the model."""
    stats = {}
    pts = [(h.get("month"), h["units"]) for h in history if h.get("units") is not None]
    if pts:
        vals = [u for _, u in pts]
        stats["months_in_history"] = len(vals)
        stats["average_units"] = round(sum(vals) / len(vals), 2)
        stats["max_units"] = {"month": max(pts, key=lambda p: p[1])[0], "units": max(vals)}
        stats["min_units"] = {"month": min(pts, key=lambda p: p[1])[0], "units": min(vals)}
        stats["months_above_100"] = sum(v > 100 for v in vals)
        stats["months_above_200"] = sum(v > 200 for v in vals)
        stats["months_above_300"] = sum(v > 300 for v in vals)
    amts = [h["bill_amount"] for h in history if h.get("bill_amount") is not None]
    if amts:
        stats["average_bill_amount"] = round(sum(amts) / len(amts), 2)
    tt, cb = bill.get("total_taxes"), bill.get("current_bill")
    if tt is not None and cb:
        stats["taxes_percent_of_current_bill"] = round(tt / cb * 100, 2)
    tc = bill.get("total_charges")
    u = bill.get("units_consumed")
    if cb is not None and u:
        stats["current_bill_per_unit"] = round(cb / u, 2)
    if tc is not None and u:
        stats["charges_per_unit"] = round(tc / u, 2)

    # ---- extra stats computed in Python ----
    def mon(h):
        try:
            return int(str(h.get("month"))[5:7])
        except Exception:
            return None
    ok = [h for h in history if h.get("units") is not None]
    summer = [(h["month"], h["units"]) for h in ok if mon(h) in (6, 7, 8)]
    winter = [(h["month"], h["units"]) for h in ok if mon(h) in (12, 1, 2)]
    if summer and winter:
        s = sum(u for _, u in summer); w = sum(u for _, u in winter)
        stats["summer_months_used"] = summer
        stats["winter_months_used"] = winter
        stats["summer_total_units"] = s
        stats["winter_total_units"] = w
        stats["summer_minus_winter_total_units"] = s - w
        stats["summer_avg_units_per_month"] = round(s / len(summer), 2)
        stats["winter_avg_units_per_month"] = round(w / len(winter), 2)
        stats["summer_avg_minus_winter_avg"] = round(s / len(summer) - w / len(winter), 2)
    bm = bill.get("bill_month")
    if bm and len(bm) == 7:
        prev_year = f"{int(bm[:4]) - 1}{bm[4:]}"
        m = [h for h in history if h.get("month") == prev_year]
        stats["same_month_last_year"] = m[0] if m else "NOT SHOWN on the bill"
        stats["current_month_is_in_history"] = any(h.get("month") == bm for h in history)
        prev_months = sorted([h for h in ok if h["month"] < bm], key=lambda h: h["month"])
        if prev_months and cb and u:
            last = prev_months[-1]
            stats["last_month_units"] = last
            stats["estimate_bill_same_units_as_last_month"] = round(last["units"] * cb / u, 2)
            stats["estimate_method"] = "last month units x (current_bill / current units)"
    if ok:
        mx = max(ok, key=lambda h: h["units"])
        stats["highest_units_month_in_history"] = {"month": mx["month"], "units": mx["units"]}
    return stats


def answer_questions(path, questions, bill=None):
    bill = bill or extract_bill(path)
    history = extract_history(path)
    stats = compute_stats(bill, history)
    qlist = "\n".join(f"{qid}: {q}" for qid, q in questions)
    prompt = f"""
You answer customer questions about the electricity bill in the image, in plain English.
Extracted bill data (JSON): {json.dumps(bill)}
Bill history (month, units, amount): {json.dumps(history)}
Python-computed stats: {json.dumps(stats)}

RULES:
- Use ONLY information on the bill. No outside tariff or rate knowledge.
- Use the computed numbers above for averages, counts and percentages. For other maths, show the numbers you used.
- If the bill does not show something, say so clearly. Do not guess.
- Label any estimate clearly as an ESTIMATE and explain how it was calculated.
- If a question is ambiguous, state which meaning you used, or answer both.
- Use the computed summer/winter, same-month-last-year, highest-month and estimate numbers EXACTLY as given; never redo that maths. If a comparison uses a different number of months, say so.\n- When counting months, say whether the current month is included.
- Answer in English only. Never include names, addresses, IDs or meter numbers.
- Style: give the number first, then short context, then a brief breakdown.

Questions:
{qlist}

Return ONLY JSON: {{"answers": {{"Q1": "...", "Q2": "..."}}}} with one key per question id.
"""
    data = call_model([load_image(path), prompt])
    return data["answers"]


def find_image(bills_dir, bid):
    return next(iter(glob.glob(f"{bills_dir}/{bid}.*")), None)


def run_pipeline(bills_dir, level1_in, level2_in, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    # ---------- Level 1 ----------
    with open(level1_in, newline="", encoding="utf-8") as f:
        rows1 = list(csv.DictReader(f))
    bill_cache = {}
    for row in rows1:
        bid = row["bill_id"]
        path = find_image(bills_dir, bid)
        result = None
        for attempt in range(3):
            try:
                result = extract_bill(path)
                break
            except Exception as e:
                print("retry L1", bid, attempt, e)
                time.sleep(10)
        if result is None:
            result = clean({})  # all-null fallback, never an empty cell
        bill_cache[bid] = result
        row["json"] = json.dumps(result, ensure_ascii=False)
        print("L1 done", bid)
        time.sleep(4)  # stay under 15 requests/min

    with open(f"{out_dir}/level1.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["bill_id", "json"])
        w.writeheader()
        w.writerows([{"bill_id": r["bill_id"], "json": r["json"]} for r in rows1])

    # ---------- Level 2 ----------
    with open(level2_in, newline="", encoding="utf-8") as f:
        rows2 = list(csv.DictReader(f))
    by_bill = {}
    for r in rows2:
        by_bill.setdefault(r["bill_id"], []).append(r)

    for bid, rs in by_bill.items():
        path = find_image(bills_dir, bid)
        qs = [(r["question_id"], r["question"]) for r in rs]
        answers = None
        for attempt in range(3):
            try:
                answers = answer_questions(path, qs, bill_cache.get(bid))
                break
            except Exception as e:
                print("retry L2", bid, attempt, e)
                time.sleep(10)
        for r in rs:
            a = (answers or {}).get(r["question_id"])
            r["answer"] = a if a else "The bill does not provide enough readable information to answer this question."
        print("L2 done", bid)
        time.sleep(4)

    with open(f"{out_dir}/level2.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["bill_id", "question_id", "question", "answer"])
        w.writeheader()
        w.writerows([{k: r[k] for k in ["bill_id", "question_id", "question", "answer"]} for r in rows2])

    print("DONE. Files saved in", out_dir)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit("Usage: python main.py <bills_dir> <level1.csv> <level2.csv> <output_dir>")
    run_pipeline(*sys.argv[1:5])
