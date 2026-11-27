````markdown
# Policy-Aware RAG Copilot for Last-Mile Logistics Compliance

An enterprise-grade compliance decision-support engine built for last-mile logistics operations. Unlike standard "Chat-with-PDF" wrappers that allow LLMs to guess dates, math thresholds, or policy exceptions, this system separates **Temporal Hybrid Retrieval** (`PostgreSQL` + `pgvector` + `TSVector`) from a **Deterministic Python Rule & Precedence Engine**, restricting the LLM strictly to evidence-grounded explanations.

---

## Core Engineering Architecture

```text
[Incident Ticket: Date + Structured Facts + Optional Free Text]
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. Temporal Hybrid Retrieval (PostgreSQL / Neon)            │
│    • Hard SQL filter: valid_from <= incident_date <= valid_to│
│    • Semantic similarity (pgvector cosine distance)         │
│    • Lexical search (TSVector full-text rank)               │
│    • Merged via Reciprocal Rank Fusion (RRF)                │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Deterministic Python Compliance Engine (Zero LLM Math)   │
│    • Gate 1: Out-of-scope abstention check                  │
│    • Gate 2: Clause-level JSONB condition evaluation        │
│    • Gate 3: Missing-field clarification detection          │
│    • Gate 4: Priority-based override resolution (100 vs 10) │
│    • Gate 5: Exact action code computation                  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Grounded Explanation & Audit Logging                     │
│    • LLM explains the Python-locked decision using only     │
│      verified clause citations                              │
│    • Logs decision JSON & measured latency to PostgreSQL    │
└─────────────────────────────────────────────────────────────┘
```
````

---

## Key Capabilities & Design Decisions

1. **Temporal Policy Versioning (Append-Only Audit History):** Policies are stored with `valid_from`, `valid_to`, and `version_num`. Historical rules are never overwritten (`ON CONFLICT DO NOTHING`), ensuring incidents are evaluated strictly against the rules active on the incident date.
2. **Clause-Level Business Conditions (`JSONB`):** Applicability rules (e.g., `{"issue_type": "customer_unavailable", "product_type": "electronics", "min_value": 15000}`) are stored per clause and indexed with PostgreSQL GIN indexes.
3. **Deterministic Decision & Precedence Engine:** Python evaluates thresholds (`max_attempts`, `max_cash_inr`, `max_distance_km`) and resolves intentional overrides (`scope="OVERRIDE", priority=100`) over general SOPs (`scope="GLOBAL", priority=10`) before the LLM generates text.
4. **Missing-Information & Out-of-Scope Guardrails:** Pauses with `NEEDS_CLARIFICATION` when critical incident attributes (such as `parcel_value` or `attempts_completed`) are missing, and abstains (`ABSTAIN_OUT_OF_SCOPE`) when queries fall outside documented SOP coverage.
5. **Structured + Free-Text API & Minimalist Console:** FastAPI backend (`POST /api/evaluate`, `GET /api/policies`, `GET /api/tickets`) paired with a lightweight vanilla HTML/JS operational console.

---

## 50-Case Evaluation Benchmark

Evaluated across 50 test cases spanning historical policy cutovers, VIP/supervisor overrides, threshold edge cases, missing-field tickets, and out-of-scope queries (`evals/run_benchmark.py`):

| Metric / Test Category              | Naive RAG Baseline (No Date Filter) | Policy-Aware Copilot |
| ----------------------------------- | ----------------------------------- | -------------------- |
| **Overall Decision Accuracy**       | 90.0%                               | **100.0% (50/50)**   |
| **1. Temporal Version Accuracy**    | 66.7%                               | **100.0% (12/12)**   |
| **2. Override Precedence Accuracy** | 100.0%                              | **100.0% (10/10)**   |
| **3. Standard Rule Accuracy**       | 100.0%                              | **100.0% (12/12)**   |
| **4. Missing-Info Clarification**   | 87.5%                               | **100.0% (8/8)**     |
| **5. Out-of-Scope Abstention**      | 100.0%                              | **100.0% (8/8)**     |
| **Retrieval Recall@5**              | —                                   | **100.0% (34/34)**   |

- **Measured Retrieval + Decision Latency:** Avg `2,048 ms` | P95 `3,055 ms` (includes cloud embedding API + Neon PostgreSQL RRF query + Python rule evaluation).

---

## Tech Stack

- **Backend & Validation:** Python, FastAPI, Uvicorn, Pydantic
- **Database & Search:** PostgreSQL (Neon), `pgvector` (768-dim cosine similarity), `TSVector` GIN full-text index, Reciprocal Rank Fusion (RRF)
- **AI Models:** Google `gemini-embedding-001` (embeddings), Gemini / Groq JSON-mode LLMs (grounded explanation)
- **Frontend:** Vanilla HTML5, CSS3, JavaScript (served via FastAPI)

---

## Project Structure

```text
policy-aware-rag-copilot/
├── app/
│   ├── config.py           # Environment configuration & model switchboard
│   ├── db.py               # PostgreSQL connection pool (psycopg3)
│   ├── llm_client.py       # Normalized embeddings & JSON-enforced LLM wrapper
│   ├── ingest.py           # Idempotent, append-only SOP ingestion pipeline
│   ├── retrieval.py        # Temporal hybrid search (pgvector + TSVector + RRF)
│   ├── decision_engine.py  # Deterministic condition, precedence & abstention engine
│   ├── copilot.py          # End-to-end orchestrator, citation verifier & latency logger
│   └── main.py             # FastAPI REST endpoints & static UI mount
├── data/sops/
│   └── policies.json       # Versioned last-mile logistics SOPs with JSONB conditions
├── evals/
│   ├── dataset_50.json     # 50-case temporal, override, clarification & abstention suite
│   ├── run_benchmark.py    # Automated evaluation harness vs. Naive RAG baseline
│   └── benchmark_results.json
├── static/
│   └── index.html          # Minimalist compliance console & policy version registry
└── requirements.txt

```

---

## Local Setup & Execution

1. **Clone and create virtual environment:**

```bash
python -m venv venv
# Windows PowerShell:
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

```

2. **Configure `.env`:**

```env
DATABASE_URL=postgresql://<user>:<password>@<neon-host>/<dbname>?sslmode=require
GEMINI_API_KEY=your_api_key_here
LLM_PROVIDER=gemini

```

3. **Ingest versioned SOP policies:**

```bash
python -m app.ingest

```

4. **Start FastAPI server & open console:**

```bash
python -m uvicorn app.main:app --reload

```

Open `http://127.0.0.1:8000` for the Compliance Console or `http://127.0.0.1:8000/docs` for Swagger API docs. 5. **Run the 50-case evaluation benchmark:**

```bash
python -m evals.run_benchmark

```

```

```
