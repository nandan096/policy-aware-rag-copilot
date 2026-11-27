import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app.db import init_pool, close_pool, get_db_cursor
from app.copilot import evaluate_incident

logger = logging.getLogger(__name__)

# Simple in-memory per-IP sliding-window rate limiter (protects Gemini quota & audit logs)
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _enforce_rate_limit(request: Request, bucket_name: str, max_requests: int, window_seconds: int = 60) -> None:
    forwarded = request.headers.get("x-forwarded-for")
    client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    key = f"{bucket_name}:{client_ip}"
    now = time.monotonic()
    q = _rate_buckets[key]

    while q and now - q[0] > window_seconds:
        q.popleft()

    if len(q) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({max_requests} requests per {window_seconds}s). Please try again shortly.",
        )
    q.append(now)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()
    yield
    close_pool()


app = FastAPI(
    title="Policy-Aware Logistics Compliance Copilot",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class IncidentRequest(BaseModel):
    incident_date: date = Field(..., description="Date of the delivery incident (YYYY-MM-DD)")
    free_text: str = Field(default="", max_length=500, description="Optional incident notes from rider/supervisor")
    issue_type: str | None = Field(default=None, description="e.g., customer_unavailable, cod_payment, damaged_parcel, open_box_delivery, address_change, reschedule_request")
    product_type: str | None = Field(default=None, description="e.g., electronics, apparel, general")
    parcel_value: float | None = Field(default=None, ge=0, description="Parcel value in INR")
    attempts_completed: int | None = Field(default=None, ge=0, description="Total failed delivery attempts completed so far (including initial delivery)")
    distance_km: float | None = Field(default=None, ge=0, description="Distance from original pin code in km")
    seal_intact: bool | None = Field(default=None, description="Whether inner security seal is intact")
    supervisor_approved: bool | None = Field(default=None, description="Whether hub supervisor approved exception")
    otp_verified: bool | None = Field(default=None, description="Whether customer verified reschedule via OTP")
    otp_shared: bool | None = Field(default=None, description="Whether customer already shared delivery OTP")


@app.get("/", include_in_schema=False)
def serve_ui() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    with get_db_cursor() as cur:
        cur.execute("SELECT 1 AS ok;")
        cur.fetchone()
    return {"status": "healthy", "database": "connected"}


@app.post("/api/evaluate")
def evaluate_ticket(req: IncidentRequest, request: Request) -> dict[str, Any]:
    _enforce_rate_limit(request, bucket_name="evaluate", max_requests=20, window_seconds=60)
    facts = req.model_dump(exclude={"incident_date", "free_text"}, exclude_none=True)
    try:
        return evaluate_incident(
            incident_date=req.incident_date,
            incident_facts=facts,
            free_text=req.free_text,
            log_to_db=True,
        )
    except Exception as exc:
        logger.exception("Incident evaluation failed")
        raise HTTPException(
            status_code=500,
            detail="Internal server error while evaluating incident.",
        ) from exc


@app.get("/api/policies")
def list_policies() -> list[dict[str, Any]]:
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT
                sd.sop_code,
                sd.title AS sop_title,
                sd.category,
                rv.version_num,
                rv.rule_clause_id,
                rv.scope,
                rv.priority,
                rv.conditions,
                rv.decision_rule,
                rv.content,
                rv.valid_from,
                rv.valid_to
            FROM rule_versions rv
            JOIN sop_documents sd ON rv.sop_id = sd.id
            ORDER BY sd.sop_code ASC, rv.version_num ASC, rv.rule_clause_id ASC;
            """
        )
        rows = cur.fetchall()
        for r in rows:
            r["valid_from"] = str(r["valid_from"])
            r["valid_to"] = str(r["valid_to"]) if r["valid_to"] else "ACTIVE"
        return list(rows)


@app.get("/api/tickets")
def recent_tickets(request: Request, limit: int = Query(default=15, ge=1, le=50)) -> list[dict[str, Any]]:
    _enforce_rate_limit(request, bucket_name="tickets", max_requests=60, window_seconds=60)
    with get_db_cursor() as cur:
        cur.execute(
            """
            SELECT id, ticket_date, query_text, decision_json, latency_ms, created_at
            FROM tickets
            ORDER BY id DESC
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()
        for r in rows:
            r["ticket_date"] = str(r["ticket_date"])
            r["created_at"] = str(r["created_at"])
        return list(rows)