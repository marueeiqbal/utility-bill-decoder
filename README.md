# ⚡ Utility Bill Decoder

Turn a photo of a Pakistani electricity bill (**K-Electric, LESCO, IESCO**) into structured data and plain-English answers, using Gemini for reading and Python for every calculation.

- **Level 1:** one JSON object per bill (provider, tariff, dates, readings, charges, taxes, payable amounts).
- **Level 2:** plain-English answers to customer questions about each bill.

## Architecture

### System overview

```mermaid
flowchart TB
    IMG[/"Bill images<br/>KE · LESCO · IESCO"/]
    T1[/"level1 template.csv"/]
    T2[/"level2 template.csv<br/>12 questions per bill"/]

    subgraph L1["Level 1 · Extraction"]
        EB["extract_bill()"] --> CL["clean()<br/>fix types · CR to negative · drop zero lines"]
    end

    subgraph L2["Level 2 · Question answering"]
        EH["extract_history()"] --> CS["compute_stats()<br/>all arithmetic in Python"] --> AQ["answer_questions()"]
    end

    G[["Google Gemini API<br/>gemini-3.1-flash-lite"]]

    O1[("level1.csv")]
    O2[("level2.csv")]

    IMG --> EB
    T1 --> EB
    IMG --> EH
    T2 --> AQ
    EB <--> G
    EH <--> G
    AQ <--> G
    CL --> O1
    CL -. "bill JSON reused" .-> AQ
    AQ --> O2
```

### Request flow for one bill

```mermaid
sequenceDiagram
    autonumber
    participant U as User / CLI
    participant M as main.py
    participant G as Gemini API
    participant F as CSV files

    U->>M: python main.py bills/ level1.csv level2.csv output/
    loop each bill in level1 template
        M->>G: bill image + extraction prompt
        G-->>M: JSON (charges, taxes, readings, dates)
        M->>M: clean()
    end
    M->>F: write level1.csv
    loop each bill in level2 template
        M->>G: bill image + history prompt
        G-->>M: history (month, units, amount)
        M->>M: compute_stats()
        M->>G: image + bill JSON + history + stats + all questions
        G-->>M: answers (one per question id)
    end
    M->>F: write level2.csv
```

### Division of labour

| Gemini (reading and wording) | Python (correctness) |
|---|---|
| Reads the bill image | `clean()`: numbers, CR credits, nulls |
| Classifies charges and taxes | `compute_stats()`: averages, counts, percentages, estimates |
| Reads the history table | Retries and fallback values |
| Writes plain-English answers | `csv` module writes the files |

### Error handling

```mermaid
flowchart TD
    S([Start bill]) --> C[Call Gemini]
    C --> OK{Valid JSON?}
    OK -- yes --> CL[clean and store]
    OK -- no --> R{Attempts left?}
    R -- yes --> W[Wait 10 s] --> C
    R -- no --> FB["Safe fallback<br/>all fields null / standard answer"]
    CL --> D([Write CSV row])
    FB --> D
```

## Quick start

Python 3.11+ (developed on 3.13).

```bash
cd source
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then put your real API key in .env
```

```bash
python main.py <bills_dir> <level1_template.csv> <level2_template.csv> <output_dir>

# example
python main.py ../test/bills ../test/csv/level1.csv ../test/csv/level2.csv ../output
```

This writes `level1.csv` and `level2.csv`, keeping `bill_id`, `question_id`, `question` and row order from the templates. Sample results are in [`output/`](output/).

### Environment variables

| Variable | Value |
|---|---|
| `MODEL_PROVIDER` | `google` |
| `MODEL_NAME` | `gemini-3.1-flash-lite` |
| `API_KEY` | Google AI Studio API key (placeholder in `.env.example`) |

## How it works

1. **Level 1 extraction.** The bill image and a detailed prompt go to the model, which returns JSON. `clean()` converts strings such as `54770CR` to numbers, removes zero or blank lines and makes sure every field exists.
2. **Level 2 answers.** A second call extracts the history table (month, units, amount). Python computes all statistics. The model then receives the image, the Level 1 JSON, the history and the computed numbers, and answers all 12 questions in one call, grounded only in the bill.
3. **Robustness.** Each bill is retried up to 3 times (including rate limits). If it still fails, safe fallback values are written, so the CSVs never contain empty cells and one bad bill never stops the run.

## Design decisions and limitations

- K-Electric bills with separate Off Peak and Peak registers use the sum of both. For LESCO and IESCO the register whose difference equals the billed units is used. Import/Export registers are ignored.
- If a bill prints only one combined tax amount, `taxes` is `[]` and the amount goes in `total_taxes`.
- Blurry or tilted photos may produce a `null` rather than a guess.

## AI usage

- **Model in the solution:** Google Gemini 3.1 Flash-Lite (`gemini-3.1-flash-lite`) via the Google AI Studio API (free tier). No other LLM is called.
- **Services:** Google AI Studio (API key), Google Colab (development).
- **OCR libraries / invoice-extraction services:** none.
- **AI tools used to help write the code:** Claude (Anthropic) for guidance, prompt design and code drafting.

## Repo layout

```
source/        main.py, requirements.txt, .env.example
output/        sample level1.csv and level2.csv
docs/images/   presentation diagrams for sharing (SVG + PNG, 1200x627)
```
