import logging
import time
from datetime import date
from typing import Any
from psycopg.types.json import Jsonb
from app.db import get_db_cursor
from app.retrieval import retrieve_applicable_clauses
from app.decision_engine import evaluate_compliance
from app.llm_client import generate_json

logger = logging.getLogger(__name__)


def _build_search_query(free_text: str, incident_facts: dict[str, Any]) -> str:
    """Combines structured facts with optional free text for stronger hybrid retrieval."""
    parts = []
    if free_text and free_text.strip():
        parts.append(free_text.strip())

    fact_labels = {
        "issue_type": "Issue",
        "product_type": "Product",
        "parcel_value": "Value INR",
        "attempts_completed": "Attempts completed",
        "distance_km": "Distance km",
    }
    for key, label in fact_labels.items():
        if incident_facts.get(key) is not None:
            parts.append(f"{label}: {incident_facts[key]}")

    return " | ".join(parts) if parts else "General logistics SOP compliance query"


def _format_clause_summary(clause: dict[str, Any] | None) -> dict[str, Any] | None:
    if not clause:
        return None
    return {
        "sop_code": clause["sop_code"],
        "sop_title": clause["sop_title"],
        "rule_clause_id": clause["rule_clause_id"],
        "version_num": clause["version_num"],
        "scope": clause["scope"],
        "priority": clause["priority"],
        "valid_from": str(clause["valid_from"]),
        "valid_to": str(clause["valid_to"]) if clause["valid_to"] else "ACTIVE",
        "content": clause["content"],
    }


def _generate_grounded_explanation(
    query_text: str,
    incident_date: date,
    incident_facts: dict[str, Any],
    decision_result: dict[str, Any],
) -> dict[str, Any]:
    """Asks the LLM to explain the Python-locked decision using only the cited clauses."""
    winner = decision_result["winning_clause"]
    overridden = decision_result.get("overridden_clauses", [])

    overridden_block = "None"
    if overridden:
        overridden_block = "\n".join(
            f"- {c['rule_clause_id']} ({c['sop_title']}, priority={c['priority']}): {c['content']}"
            for c in overridden
        )

    prompt = f"""
Incident Date: {incident_date}
Incident Notes: {query_text}
Structured Facts: {incident_facts}

LOCKED COMPLIANCE DECISION (Computed by Deterministic Rule Engine — DO NOT CHANGE):
- Decision Code: {decision_result['decision_code']}
- Winning Clause ID: {winner['rule_clause_id']} ({winner['sop_title']}, scope={winner['scope']}, priority={winner['priority']})
- Winning Clause Text: "{winner['content']}"
- Overridden Lower-Priority Clauses:
{overridden_block}

Task:
Write a clear, concise operational instruction for a last-mile hub supervisor explaining:
1. What action to take right now (matching the Decision Code).
2. Why this rule applies on {incident_date} and, if an override occurred, why it took precedence over the general policy.
3. Return strict JSON with keys:
   - "explanation": string (2-3 crisp sentences, grounded strictly in the clause text)
   - "cited_clause_ids": list of exact clause IDs referenced (must include "{winner['rule_clause_id']}")
"""

    llm_out = generate_json(prompt)
    cited = llm_out.get("cited_clause_ids", [])

    # Citation verification guardrail: ensure winning clause is always cited & strip fake IDs
    valid_ids = {winner["rule_clause_id"]} | {c["rule_clause_id"] for c in overridden}
    verified_citations = [cid for cid in cited if cid in valid_ids]
    if winner["rule_clause_id"] not in verified_citations:
        verified_citations.insert(0, winner["rule_clause_id"])

    return {
        "explanation": llm_out.get("explanation", decision_result["reason"]),
        "cited_clause_ids": verified_citations,
    }


def evaluate_incident(
    incident_date: date,
    incident_facts: dict[str, Any] | None = None,
    free_text: str = "",
    log_to_db: bool = True,
) -> dict[str, Any]:
    """End-to-end Policy-Aware RAG Copilot pipeline."""
    start_time = time.perf_counter()
    facts = {k: v for k, v in (incident_facts or {}).items() if v is not None}
    search_query = _build_search_query(free_text, facts)

    # 1. Temporal Hybrid Retrieval
    retrieved = retrieve_applicable_clauses(
        query_text=search_query,
        incident_date=incident_date,
        top_k=5,
    )

    # 2. Deterministic Python Rule & Precedence Evaluation
    decision = evaluate_compliance(retrieved, facts)

    # 3. Grounded Explanation with deterministic fallback if LLM provider fails
    if decision["status"] == "DECIDED":
        winner = decision["winning_clause"]
        try:
            grounded = _generate_grounded_explanation(search_query, incident_date, facts, decision)
            explanation = grounded["explanation"]
            cited_clauses = grounded["cited_clause_ids"]
        except Exception as exc:
            logger.warning("LLM explanation failed; falling back to rule reason: %s", exc)
            explanation = f"{decision['reason']} Policy clause: {winner['content']}"
            cited_clauses = [winner["rule_clause_id"]]
    elif decision["status"] == "NEEDS_CLARIFICATION":
        missing_str = ", ".join(decision["missing_fields"])
        explanation = f"Additional incident details needed before making a compliance decision: please provide {missing_str}."
        cited_clauses = []
    else:
        explanation = f"Escalating to human supervisor: {decision['reason']}"
        cited_clauses = []

    latency_ms = int((time.perf_counter() - start_time) * 1000)

    response_payload = {
        "incident_date": str(incident_date),
        "status": decision["status"],
        "decision_code": decision["decision_code"],
        "explanation": explanation,
        "cited_clause_ids": cited_clauses,
        "missing_fields": decision["missing_fields"],
        "winning_clause": _format_clause_summary(decision["winning_clause"]),
        "overridden_clauses": [_format_clause_summary(c) for c in decision["overridden_clauses"]],
        "latency_ms": latency_ms,
    }

    # 4. Best-effort audit log to Neon tickets table (never discard a valid decision on log failure)
    if log_to_db:
        try:
            with get_db_cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tickets (ticket_date, query_text, decision_json, latency_ms)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (incident_date, search_query, Jsonb(response_payload), latency_ms),
                )
        except Exception as exc:
            logger.warning("Failed to persist audit ticket to Neon: %s", exc)

    return response_payload