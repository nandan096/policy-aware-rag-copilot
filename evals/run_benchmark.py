import json
import time
from datetime import date
from pathlib import Path
from app.db import init_pool, close_pool
from app.retrieval import retrieve_applicable_clauses
from app.decision_engine import evaluate_compliance
from app.copilot import _build_search_query


def _run_naive_baseline(clauses: list[dict], facts: dict) -> dict:
    """
    Simulates a Naive RAG baseline:
    - Retrieved with ignore_dates=True (no temporal filtering)
    - Ignores policy priority/overrides (forces all clauses to priority=10)
    """
    naive_clauses = [{**c, "priority": 10} for c in clauses]
    return evaluate_compliance(naive_clauses, facts)


def run_benchmark(dataset_path: str = "evals/dataset_50.json") -> None:
    cases = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    init_pool()

    totals = {"all": 0, "temporal": 0, "override": 0, "standard": 0, "clarification": 0, "abstention": 0}
    copilot_hits = {k: 0 for k in totals}
    baseline_hits = {k: 0 for k in totals}
    recall_hits = 0
    recall_total = 0
    latencies_ms: list[float] = []
    failures: list[dict] = []

    try:
        for tc in cases:
            cat = tc["category"]
            totals["all"] += 1
            totals[cat] += 1

            inc_date = date.fromisoformat(tc["incident_date"])
            facts = tc["facts"]
            query = _build_search_query(tc["free_text"], facts)

            # 1. Policy-Aware Copilot (Temporal Hybrid Search + Deterministic Priority Engine)
            t0 = time.perf_counter()
            temporal_clauses = retrieve_applicable_clauses(query, inc_date, top_k=5, ignore_dates=False)
            copilot_res = evaluate_compliance(temporal_clauses, facts)
            latencies_ms.append((time.perf_counter() - t0) * 1000)

            # 2. Naive RAG Baseline (No Temporal Filter + No Priority Override)
            naive_clauses = retrieve_applicable_clauses(query, inc_date, top_k=5, ignore_dates=True)
            baseline_res = _run_naive_baseline(naive_clauses, facts)

            # Check Retrieval Recall@5 for cases with a target clause
            if tc["expected_clause"]:
                recall_total += 1
                retrieved_ids = [c["rule_clause_id"] for c in temporal_clauses]
                if tc["expected_clause"] in retrieved_ids:
                    recall_hits += 1

            # Check Decision Accuracy
            c_ok = (
                copilot_res["status"] == tc["expected_status"]
                and copilot_res["decision_code"] == tc["expected_decision"]
            )
            b_ok = (
                baseline_res["status"] == tc["expected_status"]
                and baseline_res["decision_code"] == tc["expected_decision"]
            )

            if c_ok:
                copilot_hits["all"] += 1
                copilot_hits[cat] += 1
            else:
                failures.append({
                    "id": tc["id"],
                    "category": cat,
                    "expected": tc["expected_decision"],
                    "got": copilot_res["decision_code"],
                    "reason": copilot_res["reason"],
                })

            if b_ok:
                baseline_hits["all"] += 1
                baseline_hits[cat] += 1

    finally:
        close_pool()

    def pct(num: int, den: int) -> str:
        return f"{(num / den * 100):.1f}%" if den else "N/A"

    avg_lat = sum(latencies_ms) / len(latencies_ms)
    p95_lat = sorted(latencies_ms)[int(0.95 * len(latencies_ms)) - 1]

    print("\n" + "=" * 72)
    print("POLICY-AWARE RAG COPILOT — 50-CASE EVALUATION BENCHMARK")
    print("=" * 72)
    print(f"{'Metric / Category':<30} | {'Naive RAG':<12} | {'Policy-Aware Copilot'}")
    print("-" * 72)
    print(f"{'Overall Decision Accuracy':<30} | {pct(baseline_hits['all'], totals['all']):<12} | {pct(copilot_hits['all'], totals['all'])} ({copilot_hits['all']}/{totals['all']})")
    print(f"{'1. Temporal Version Accuracy':<30} | {pct(baseline_hits['temporal'], totals['temporal']):<12} | {pct(copilot_hits['temporal'], totals['temporal'])} ({copilot_hits['temporal']}/{totals['temporal']})")
    print(f"{'2. Override Precedence Acc.':<30} | {pct(baseline_hits['override'], totals['override']):<12} | {pct(copilot_hits['override'], totals['override'])} ({copilot_hits['override']}/{totals['override']})")
    print(f"{'3. Standard Rule Accuracy':<30} | {pct(baseline_hits['standard'], totals['standard']):<12} | {pct(copilot_hits['standard'], totals['standard'])} ({copilot_hits['standard']}/{totals['standard']})")
    print(f"{'4. Missing-Info Clarification':<30} | {pct(baseline_hits['clarification'], totals['clarification']):<12} | {pct(copilot_hits['clarification'], totals['clarification'])} ({copilot_hits['clarification']}/{totals['clarification']})")
    print(f"{'5. Out-of-Scope Abstention':<30} | {pct(baseline_hits['abstention'], totals['abstention']):<12} | {pct(copilot_hits['abstention'], totals['abstention'])} ({copilot_hits['abstention']}/{totals['abstention']})")
    print("-" * 72)
    print(f"Retrieval Recall@5 (Policy-Aware): {pct(recall_hits, recall_total)} ({recall_hits}/{recall_total})")
    print(f"Retrieval + Decision Latency:      Avg = {avg_lat:.0f} ms | P95 = {p95_lat:.0f} ms")
    print("=" * 72)

    if failures:
        print("\nMisclassified Cases:")
        for f in failures:
            print(f"  - {f['id']} ({f['category']}): expected {f['expected']}, got {f['got']} ({f['reason']})")

    report = {
        "total_cases": totals["all"],
        "naive_baseline_accuracy_pct": round(baseline_hits["all"] / totals["all"] * 100, 2),
        "copilot_accuracy_pct": round(copilot_hits["all"] / totals["all"] * 100, 2),
        "temporal_accuracy_pct": round(copilot_hits["temporal"] / totals["temporal"] * 100, 2),
        "override_accuracy_pct": round(copilot_hits["override"] / totals["override"] * 100, 2),
        "clarification_accuracy_pct": round(copilot_hits["clarification"] / totals["clarification"] * 100, 2),
        "abstention_accuracy_pct": round(copilot_hits["abstention"] / totals["abstention"] * 100, 2),
        "retrieval_recall_at_5_pct": round(recall_hits / recall_total * 100, 2),
        "avg_retrieval_decision_latency_ms": round(avg_lat, 1),
        "p95_retrieval_decision_latency_ms": round(p95_lat, 1),
        "failures": failures,
    }
    Path("evals/benchmark_results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nSaved detailed benchmark report to evals/benchmark_results.json")


if __name__ == "__main__":
    run_benchmark()