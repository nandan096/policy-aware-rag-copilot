# Policy-Aware RAG Copilot for Last-Mile Logistics

A decision-support API and web console for evaluating last-mile delivery incidents against versioned logistics policies. It combines date-aware hybrid retrieval with a deterministic Python rules engine, so an LLM can explain a decision but does not choose the compliance action.

> **Scope:** This project is a technical demo, not legal advice or a replacement for an organization’s approved operating procedures.

## Why policy-aware retrieval?

Policies can change over time, and specific exceptions can override general rules. Retrieving similar text alone is not enough: the incident date, structured facts, and clause priority also matter. This project makes those steps explicit and returns a decision together with the clause evidence used to reach it.

## How it works

1. **Build a search query** from the incident’s free text and structured facts.
2. **Retrieve applicable policy clauses** from PostgreSQL using an incident-date validity filter, semantic similarity (`pgvector`), and keyword search (`tsvector`). Reciprocal Rank Fusion combines the two rankings.
3. **Evaluate policy conditions deterministically** in Python. Higher-priority clauses take precedence; missing required facts trigger clarification, and weak or unmatched results abstain.
4. **Explain decided outcomes** using the winning and overridden clauses as evidence. The decision code is already locked by the rules engine; returned citations are checked against the clauses supplied to the explanation step.
5. **Record an audit entry** containing the query, decision payload, and latency in the `tickets` table.

## Features

- **Temporal policy versions:** clauses have validity dates, so an incident is evaluated against policies active on that date.
- **Hybrid retrieval:** vector similarity and PostgreSQL full-text ranking are merged using Reciprocal Rank Fusion.
- **Deterministic decisions:** conditions, required fields, action codes, and priority resolution are handled in Python.
- **Clarification and abstention:** the engine can request missing facts or decline to decide when no applicable policy is found.
- **Grounded explanations:** LLM explanations are generated only after a decision and constrained to the decision and retrieved clause evidence.
- **Operational console and API:** review incident decisions, policy clauses, and recent tickets in the browser or through FastAPI endpoints.

## Evaluation snapshot

The 50-case evaluation results are summarized below:

| Metric                                   | Naive RAG baseline |           Policy-Aware Copilot |
| ---------------------------------------- | -----------------: | -----------------------------: |
| Overall decision accuracy                |              90.0% |             **100.0% (50/50)** |
| Temporal version accuracy                |              66.7% |             **100.0% (12/12)** |
| Missing-info clarification accuracy      |              87.5% |               **100.0% (8/8)** |
| Override, standard & abstention accuracy |             100.0% |             **100.0% (30/30)** |
| Retrieval Recall@5                       |                  — |             **100.0% (34/34)** |
| Retrieval + decision latency             |                  — | **2,048 ms avg; 3,055 ms P95** |

The dataset covers temporal policy changes, override precedence, standard rules, missing-information clarification, and out-of-scope abstention. The naive baseline disables date filtering and neutralizes policy priorities. These results are an evaluation snapshot, not a performance guarantee; aggregate results are saved in [`evals/benchmark_results.json`](evals/benchmark_results.json). Latency depends on the database, network, and embedding API; the benchmark measures retrieval and deterministic evaluation, not the final LLM explanation or audit logging.

Run the benchmark with:

```powershell
python -m evals.run_benchmark
```

It requires the configured database and embedding API, and rewrites `evals/benchmark_results.json`.

## Technology

- **API:** Python, FastAPI, Pydantic, Uvicorn
- **Database and retrieval:** PostgreSQL, Neon-compatible connection, `pgvector`, PostgreSQL full-text search, Reciprocal Rank Fusion
- **Embeddings:** Gemini (`gemini-embedding-001`, 768-dimensional vectors)
- **Explanations:** Gemini by default; Groq is also supported as the answering provider
- **UI:** HTML, CSS, and vanilla JavaScript served by FastAPI

## Repository layout

```text
app/
  config.py           Environment and model settings
  copilot.py          End-to-end retrieval, decision, explanation, and audit flow
  db.py               PostgreSQL connection pool
  decision_engine.py  Deterministic conditions and priority resolution
  ingest.py           Append-only policy ingestion
  llm_client.py       Gemini embeddings and Gemini/Groq JSON responses
  main.py             FastAPI routes and static file serving
  retrieval.py        Temporal hybrid retrieval and RRF ranking
data/
  sops/policies.json  Sample versioned policy clauses
evals/
  dataset_50.json     Evaluation cases
  run_benchmark.py   Benchmark harness
  benchmark_results.json
sql/
  schema.sql          PostgreSQL schema with pgvector & tsvector indexes
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
  "explanation": "Hold the shipment at the secure hub cage and initiate immediate RTO because only 1 delivery reattempt is permitted for high-value electronics above INR 15,000.",
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
    "content": "For high-value electronics shipments above INR 15,000, only 1 delivery reattempt is permitted due to theft risk. After the second failed attempt, hold at the secure hub cage and initiate immediate RTO."
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
      "content": "When a customer is unavailable at the doorstep, the delivery executive must log an IVR call and wait 10 minutes. Maximum 3 delivery reattempts are allowed within 72 hours before marking the shipment RTO (Return to Origin)."
    }
  ],
  "latency_ms": 2140
}
```

The response includes the status and decision code, explanation, cited clause IDs, missing fields, winning and overridden clause summaries, and measured latency. Possible statuses include `DECIDED`, `NEEDS_CLARIFICATION`, and `ABSTAIN`. The explanation and latency above are illustrative; actual values can vary by run.

### Other endpoints

| Method | Endpoint                | Purpose                                        |
| ------ | ----------------------- | ---------------------------------------------- |
| `GET`  | `/api/health`           | Check database connectivity                    |
| `GET`  | `/api/policies`         | List policy clauses and their version metadata |
| `GET`  | `/api/tickets?limit=15` | Return recent audit entries; defaults to 15    |

## Configuration reference

| Variable             | Required                        | Purpose                                                                  |
| -------------------- | ------------------------------- | ------------------------------------------------------------------------ |
| `DATABASE_URL`       | Yes                             | PostgreSQL connection string                                             |
| `GEMINI_API_KEY`     | Yes for ingestion and retrieval | Gemini embedding API; also used for explanations when Gemini is selected |
| `GROQ_API_KEY`       | Only when using Groq            | Groq explanation API                                                     |
| `ANSWERING_PROVIDER` | No                              | Explanation provider: `gemini` (default) or `groq`                       |
| `ANSWERING_MODEL`    | No                              | Explanation model; defaults to `gemini-3.5-flash-lite`                   |
| `EMBEDDING_MODEL`    | No                              | Embedding model; defaults to `gemini-embedding-001`                      |

## Development notes

- Policy data lives in `data/sops/policies.json`; add new policy versions rather than overwriting historical versions.
- Run `sql/schema.sql` before first-time ingestion; `python -m app.ingest` uses Gemini to generate 768-dimensional embeddings for newly inserted clauses.
- The benchmark evaluates retrieval and deterministic decisions against the sample dataset. It uses the database and embedding provider but does not exercise the LLM explanation path.

