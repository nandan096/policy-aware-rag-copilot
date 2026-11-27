# Policy-Aware RAG Copilot for Last-Mile Logistics

A decision-support API and web console for evaluating last-mile delivery incidents against versioned logistics policies. It combines date-aware hybrid retrieval with a deterministic Python rules engine, so an LLM explains a locked decision but never chooses the compliance action.

- **Live Web Console:** <https://policy-aware-rag-copilot.onrender.com/>
- **Interactive API Docs (Swagger):** <https://policy-aware-rag-copilot.onrender.com/docs>
- **Health Check:** <https://policy-aware-rag-copilot.onrender.com/api/health>

> **Note:** Hosted on Render’s free tier—initial requests after an idle period may take ~50 seconds for the container to spin up.

> **Scope:** This project is a technical demo, not legal advice or a replacement for an organization’s approved operating procedures.

## Why policy-aware retrieval?

Policies change over time, and specific exceptions override general rules. Retrieving similar text alone is not enough: the incident date, structured facts, and clause priority also matter. This project makes those steps explicit and returns a deterministic decision together with the clause evidence used to reach it.

## How it works

1. **Build a search query** from the incident’s free text and structured facts.
2. **Retrieve applicable policy clauses** from PostgreSQL using an incident-date validity filter, semantic similarity (`pgvector`), and keyword search (`tsvector` with `OR`-expanded terms). Reciprocal Rank Fusion combines the two rankings.
3. **Evaluate policy conditions deterministically** in Python. Higher-priority clauses take precedence; equal-priority conflicts escalate to `NEEDS_REVIEW`, missing required facts trigger `NEEDS_CLARIFICATION`, and unmatched or out-of-scope queries abstain.
4. **Explain decided outcomes** using the winning and overridden clauses as evidence. `decision_code` is already locked by the rules engine; the LLM is prompted strictly on the winning/overridden clauses and returned citations are verified against supplied clause IDs (with automatic deterministic fallback if the LLM provider fails).
5. **Record a best-effort audit entry** containing the query, decision payload, and latency in the `tickets` table.

## Features

- **Temporal policy versions:** clauses have validity dates (`valid_from`, `valid_to`), so an incident is evaluated against policies active on that date.
- **Hybrid retrieval:** vector similarity and PostgreSQL full-text ranking are merged using Reciprocal Rank Fusion.
- **Deterministic decisions:** conditions, required fields, action codes, and priority resolution are handled in Python.
- **Clarification, review, and abstention:** the engine requests missing facts (`NEEDS_CLARIFICATION`), flags equal-priority conflicts (`NEEDS_REVIEW`), or declines to decide when no applicable policy matches (`ABSTAIN`).
- **Prompted & citation-checked explanations:** LLM explanations are generated only after a deterministic decision, verified against retrieved clause IDs, and backed by an automatic rule-reason fallback.
- **Operational console and API:** review incident decisions, policy clauses, and recent tickets in the browser or through FastAPI endpoints.

## Evaluation snapshot

The 60-case evaluation results are summarized below:

| Metric                              | Ablation baseline (no date / equal priority) |           Policy-Aware Copilot |
| ----------------------------------- | -------------------------------------------: | -----------------------------: |
| Overall decision accuracy           |                                        65.0% |             **100.0% (60/60)** |
| Temporal version accuracy           |                                        25.0% |             **100.0% (12/12)** |
| Override precedence accuracy        |                                        33.3% |             **100.0% (15/15)** |
| Standard rule accuracy              |                                        91.7% |             **100.0% (12/12)** |
| Missing-info clarification accuracy |                                        87.5% |               **100.0% (8/8)** |
| Out-of-scope abstention accuracy    |                                       100.0% |             **100.0% (13/13)** |
| Retrieval Recall@5                  |                                            — |             **100.0% (39/39)** |
| Retrieval + decision latency        |                                            — | **2,203 ms avg; 2,601 ms P95** |

The dataset covers temporal policy transitions, override precedence (including adversarial cases where general clauses share high lexical overlap with the query), standard rules, missing-information clarification, and out-of-scope abstention (both free-text and fact-populated). The ablation baseline runs the same deterministic evaluator with date filtering disabled and all clause priorities flattened to equal rank (`priority=10`). Aggregate results are saved in [`evals/benchmark_results.json`](evals/benchmark_results.json). Latency depends on the database, network, and embedding API; the benchmark measures retrieval and deterministic evaluation, not the final LLM explanation or audit logging.

Run the benchmark with:

```powershell
python -m evals.run_benchmark
```

It requires the configured database and embedding API, and rewrites `evals/benchmark_results.json`.

## Technology

- **API:** Python, FastAPI, Pydantic, Uvicorn
- **Database and retrieval:** PostgreSQL, Neon-compatible connection pool with health checks, `pgvector`, PostgreSQL full-text search, Reciprocal Rank Fusion
- **Embeddings:** Gemini (`gemini-embedding-001`, 768-dimensional vectors)
- **Explanations:** Gemini by default; Groq is also supported as the answering provider
- **UI:** HTML, CSS, and vanilla JavaScript served by FastAPI

## Repository layout

```text
app/
  config.py           Environment and model settings
  copilot.py          End-to-end retrieval, decision, explanation, and audit flow
  db.py               PostgreSQL connection pool with idle-connection health checks
  decision_engine.py  Deterministic conditions, conflict detection, and priority resolution
  ingest.py           Append-only policy ingestion
  llm_client.py       Gemini embeddings and Gemini/Groq JSON responses
  main.py             FastAPI routes and static file serving
  retrieval.py        Temporal hybrid retrieval and RRF ranking
data/
  sops/policies.json  Sample versioned policy clauses
evals/
  dataset_60.json     60 evaluation cases across 5 categories
  run_benchmark.py    Benchmark harness
  benchmark_results.json
sql/
  schema.sql          PostgreSQL schema with pgvector, tsvector & date constraints
static/
  index.html          Web console
  styles.css          Console styles
requirements.txt
```

## Setup

### Prerequisites

- Python 3.10 or later
- A PostgreSQL database with `pgvector` enabled (`sql/schema.sql` provisions `sop_documents`, `rule_versions`, and `tickets`)
- A Gemini API key for embeddings. The default explanation provider also uses Gemini.
- Optionally, a Groq API key if you choose Groq for explanations

### 1. Create and activate a virtual environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file in the repository root. Do not commit credentials.

```dotenv
DATABASE_URL=postgresql://<user>:<password>@<host>/<database>?sslmode=require
GEMINI_API_KEY=<your-gemini-api-key>

# Optional: use Groq instead of Gemini for generated explanations
GROQ_API_KEY=<your-groq-api-key>
ANSWERING_PROVIDER=gemini
ANSWERING_MODEL=gemini-3.5-flash-lite
EMBEDDING_MODEL=gemini-embedding-001
```

`DATABASE_URL` is required. Gemini embeddings are used for both ingestion and retrieval, so `GEMINI_API_KEY` is required even if Groq is selected for explanations. To use Groq for explanations, set `ANSWERING_PROVIDER=groq` and provide `GROQ_API_KEY`.

### 3. Initialize the database schema

Run `sql/schema.sql` against your PostgreSQL / Neon database to enable `pgvector` and create the required tables and indexes (`sop_documents`, `rule_versions`, `tickets`):

```powershell
psql "$env:DATABASE_URL" -f sql/schema.sql
```

### 4. Ingest policies

After initializing the database schema, ingest the sample policies:

```powershell
python -m app.ingest
```

Ingestion preserves existing policy versions and skips clauses that have already been inserted.

### 5. Start the API and web console

```powershell
python -m uvicorn app.main:app --reload
```

Open:

- Web console: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- Interactive API documentation: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Health check: [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)

The health endpoint checks database connectivity.

## API

### `POST /api/evaluate`

Evaluate an incident. `incident_date` is required; the other structured fields are optional and depend on the policy being evaluated.

```json
{
  "incident_date": "2026-08-10",
  "free_text": "Customer was unavailable at the delivery location.",
  "issue_type": "customer_unavailable",
  "product_type": "electronics",
  "parcel_value": 20000,
  "attempts_completed": 1
}
```

Example response:

```json
{
  "incident_date": "2026-08-10",
  "status": "DECIDED",
  "decision_code": "HOLD_AT_SECURE_CAGE_AND_RTO",
  "explanation": "Hold the shipment at the secure hub cage and initiate immediate RTO because zero reattempts are permitted after the initial failed delivery for high-value electronics valued at INR 15,000 or above.",
  "cited_clause_ids": ["SOP-VIP-002-C1", "SOP-DEL-001-C1-V2"],
  "missing_fields": [],
  "winning_clause": {
    "sop_code": "SOP-VIP-002",
    "sop_title": "High-Value Electronics Client Override SOP",
    "rule_clause_id": "SOP-VIP-002-C1",
    "version_num": 1,
    "scope": "OVERRIDE",
    "priority": 100,
    "valid_from": "2026-01-01",
    "valid_to": "ACTIVE",
    "content": "For high-value electronics shipments valued at INR 15,000 or above, zero reattempts are permitted after the initial failed delivery due to theft risk. Once 1 failed attempt is recorded, hold at the secure hub cage and initiate immediate RTO."
  },
  "overridden_clauses": [
    {
      "sop_code": "SOP-DEL-001",
      "sop_title": "Standard Last-Mile Reattempt & NDR Policy",
      "rule_clause_id": "SOP-DEL-001-C1-V2",
      "version_num": 2,
      "scope": "GLOBAL",
      "priority": 10,
      "valid_from": "2026-06-01",
      "valid_to": "ACTIVE",
      "content": "When a customer is unavailable at the doorstep, the delivery executive must log an IVR call and wait 10 minutes. A maximum of 3 failed delivery attempts (including the initial attempt) are permitted within 72 hours before marking the shipment RTO (Return to Origin)."
    }
  ],
  "latency_ms": 2140
}
```

The response includes the status and authoritative `decision_code`, explanation, cited clause IDs, missing fields, winning and overridden clause summaries, and measured latency. Possible statuses include `DECIDED`, `NEEDS_CLARIFICATION`, `NEEDS_REVIEW`, and `ABSTAIN`. The explanation and latency above are illustrative; actual values can vary by run.

### Other endpoints

| Method | Endpoint                | Purpose                                                   |
| ------ | ----------------------- | --------------------------------------------------------- |
| `GET`  | `/api/health`           | Check database connectivity                               |
| `GET`  | `/api/policies`         | List policy clauses and their version metadata            |
| `GET`  | `/api/tickets?limit=15` | Return recent audit entries (`1`–`100`); defaults to `15` |

## Configuration reference

| Variable             | Required                        | Purpose                                                                  |
| -------------------- | ------------------------------- | ------------------------------------------------------------------------ |
| `DATABASE_URL`       | Yes                             | PostgreSQL connection string                                             |
| `GEMINI_API_KEY`     | Yes for ingestion and retrieval | Gemini embedding API; also used for explanations when Gemini is selected |
| `GROQ_API_KEY`       | Only when using Groq            | Groq explanation API                                                     |
| `ANSWERING_PROVIDER` | No                              | Explanation provider: `gemini` (default) or `groq`                       |
| `ANSWERING_MODEL`    | No                              | Explanation model; defaults to `gemini-3.5-flash-lite`                   |
| `EMBEDDING_MODEL`    | No                              | Embedding model; defaults to `gemini-embedding-001`                      |

## Limitations

- **Synthetic policy corpus:** Sample policies in `data/sops/policies.json` are synthetic last-mile logistics SOPs created for demonstration, not proprietary corporate policies.
- **Tuned similarity thresholds:** Abstention thresholds (`0.62` general similarity floor and `0.76` zero-fact query floor) were calibrated against the 60-case evaluation set and may require re-tuning for larger or domain-shifted policy corpora.
- **Benchmark latency scope:** Reported benchmark latency (`2,203 ms avg; 2,601 ms P95`) covers embedding generation, hybrid PostgreSQL retrieval, and deterministic rule evaluation; end-to-end `/api/evaluate` requests add LLM explanation generation and audit logging time.
- **Public demo endpoints:** `/api/evaluate` and `/api/tickets` are publicly accessible and rate-limited on the hosted demo—do not submit sensitive, personal, or proprietary operational data.

## Development notes

- Policy data lives in `data/sops/policies.json`; add new policy versions rather than overwriting historical versions.
- Run `sql/schema.sql` before first-time ingestion; `python -m app.ingest` uses Gemini to generate 768-dimensional embeddings for newly inserted clauses.
- The benchmark evaluates retrieval and deterministic decisions against the 60-case dataset. It uses the database and embedding provider but does not exercise the LLM explanation path.
