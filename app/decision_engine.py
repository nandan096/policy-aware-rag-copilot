from typing import Any


def _check_clause_conditions(conditions: dict[str, Any], facts: dict[str, Any]) -> tuple[bool, bool]:
    """
    Checks whether incident facts satisfy a clause's conditions.
    Returns (is_relevant_candidate, is_fully_satisfied):
      - is_relevant_candidate: True if no provided fact contradicts the condition.
      - is_fully_satisfied: True if all required condition keys exist in facts and pass.
    """
    if not conditions:
        return True, True

    is_fully_satisfied = True

    for key, expected in conditions.items():
        # Handle minimum numeric threshold (e.g., min_value: 15000)
        if key == "min_value":
            actual_val = facts.get("parcel_value")
            if actual_val is None:
                is_fully_satisfied = False
                continue
            if float(actual_val) < float(expected):
                return False, False

        # Handle exact match conditions (e.g., issue_type, product_type, seal_intact)
        else:
            actual = facts.get(key)
            if actual is None:
                is_fully_satisfied = False
                continue

            if isinstance(expected, str) and isinstance(actual, str):
                if actual.strip().lower() != expected.strip().lower():
                    return False, False
            else:
                if actual != expected:
                    return False, False

    return True, is_fully_satisfied


def _execute_decision_rule(decision_rule: dict[str, Any], facts: dict[str, Any]) -> str:
    """Computes the exact deterministic action code from the winning rule."""
    # 1. Reattempt limit rules (SOP-DEL-001, SOP-VIP-002)
    if "max_attempts" in decision_rule:
        attempts = int(facts["attempts_completed"])
        max_attempts = int(decision_rule["max_attempts"])
        if attempts >= max_attempts:
            return decision_rule["action_if_limit_reached"]
        return decision_rule["action_if_under_limit"]

    # 2. COD cash limit rules (SOP-COD-003)
    if "max_cash_inr" in decision_rule:
        value = float(facts["parcel_value"])
        max_cash = float(decision_rule["max_cash_inr"])
        if value > max_cash:
            return decision_rule["action_if_over_limit"]
        return decision_rule["action_if_under_limit"]

    # 3. Distance / pin-code mismatch rules (SOP-ADR-006)
    if "max_distance_km" in decision_rule:
        dist = float(facts["distance_km"])
        max_dist = float(decision_rule["max_distance_km"])
        if dist <= max_dist:
            return decision_rule["action_if_within_limit"]
        return decision_rule["action_if_beyond_limit"]

    # 4. OTP reschedule rules (SOP-DEL-001-C2)
    if "require_otp" in decision_rule:
        if bool(facts.get("otp_verified")):
            return decision_rule["action_if_valid"]
        return decision_rule["action_if_invalid"]

    # 5. Damaged parcel seal check (SOP-DMG-004-C1)
    if "action_if_broken" in decision_rule:
        if not bool(facts.get("seal_intact")):
            return decision_rule["action_if_broken"]
        return "PROCEED_WITH_NORMAL_DELIVERY"

    # 6. Open-box delivery OTP check (SOP-DMG-004-C2)
    if "action_before_otp" in decision_rule:
        if bool(facts.get("otp_shared")):
            return decision_rule["action_after_otp"]
        return decision_rule["action_before_otp"]

    # 7. Direct override action (SOP-HUB-005-C1)
    if "action" in decision_rule:
        return decision_rule["action"]

    return "ESCALATE_TO_SUPERVISOR"


def evaluate_compliance(
    retrieved_clauses: list[dict[str, Any]],
    incident_facts: dict[str, Any],
    min_similarity_threshold: float = 0.62,
) -> dict[str, Any]:
    """
    Deterministic Policy & Precedence Engine.
    Evaluates incident facts against retrieved temporal clauses.
    """
    # Gate 1: Abstain if retrieval found nothing or semantic match is too weak
    if not retrieved_clauses:
        return {
            "status": "ABSTAIN",
            "decision_code": "ABSTAIN_NO_APPLICABLE_POLICY",
            "winning_clause": None,
            "overridden_clauses": [],
            "missing_fields": [],
            "reason": "No active SOP policies found for this incident date.",
        }

    top_similarity = float(retrieved_clauses[0].get("semantic_similarity", 0.0))
    if top_similarity < min_similarity_threshold:
        return {
            "status": "ABSTAIN",
            "decision_code": "ABSTAIN_OUT_OF_SCOPE",
            "winning_clause": None,
            "overridden_clauses": [],
            "missing_fields": [],
            "reason": f"Query falls outside documented SOP coverage (top similarity {top_similarity} < {min_similarity_threshold}).",
        }

    top_kw_score = max(float(c.get("kw_score", 0.0)) for c in retrieved_clauses)
    if not incident_facts and top_kw_score == 0.0 and top_similarity < 0.72:
        return {
            "status": "ABSTAIN",
            "decision_code": "ABSTAIN_OUT_OF_SCOPE",
            "winning_clause": None,
            "overridden_clauses": [],
            "missing_fields": [],
            "reason": "Question does not match any documented SOP policy terms or incident parameters.",
        }

    # Gate 2: Evaluate clause-level conditions
    fully_matched: list[dict[str, Any]] = []
    partial_candidates: list[dict[str, Any]] = []

    for clause in retrieved_clauses:
        conditions = clause.get("conditions") or {}
        is_candidate, is_satisfied = _check_clause_conditions(conditions, incident_facts)
        if is_candidate and is_satisfied:
            fully_matched.append(clause)
        elif is_candidate and not is_satisfied:
            partial_candidates.append(clause)

    # Sort candidates by priority descending (e.g., priority 100 OVERRIDE beats priority 10 GLOBAL)
    fully_matched.sort(key=lambda c: int(c.get("priority", 10)), reverse=True)
    partial_candidates.sort(key=lambda c: int(c.get("priority", 10)), reverse=True)

    # Gate 3: Check if a higher-priority override could apply, or no rule matched because fields are missing
    highest_matched_priority = int(fully_matched[0]["priority"]) if fully_matched else -1

    for candidate in partial_candidates:
        if int(candidate.get("priority", 10)) > highest_matched_priority:
            req_fields = (candidate.get("decision_rule") or {}).get("required_fields", [])
            missing = [f for f in req_fields if incident_facts.get(f) is None]
            if missing:
                return {
                    "status": "NEEDS_CLARIFICATION",
                    "decision_code": "CLARIFICATION_REQUIRED",
                    "winning_clause": None,
                    "overridden_clauses": [],
                    "missing_fields": missing,
                    "reason": f"Cannot evaluate {candidate['rule_clause_id']} ({candidate['sop_title']}) without: {', '.join(missing)}.",
                }

    if not fully_matched:
        return {
            "status": "ABSTAIN",
            "decision_code": "ABSTAIN_NO_CONDITION_MATCH",
            "winning_clause": None,
            "overridden_clauses": [],
            "missing_fields": [],
            "reason": "Retrieved clauses did not match the provided incident conditions.",
        }

    # Gate 4: Resolve Precedence (highest priority wins; lower matching rules are marked overridden)
    winner = fully_matched[0]
    overridden = fully_matched[1:]

    # Gate 5: Check required action fields on the winning rule
    winner_rule = winner.get("decision_rule") or {}
    missing_action_fields = [
        f for f in winner_rule.get("required_fields", []) if incident_facts.get(f) is None
    ]
    if missing_action_fields:
        return {
            "status": "NEEDS_CLARIFICATION",
            "decision_code": "CLARIFICATION_REQUIRED",
            "winning_clause": winner,
            "overridden_clauses": overridden,
            "missing_fields": missing_action_fields,
            "reason": f"Matched {winner['rule_clause_id']}, but need {', '.join(missing_action_fields)} to compute the final action.",
        }

    decision_code = _execute_decision_rule(winner_rule, incident_facts)

    return {
        "status": "DECIDED",
        "decision_code": decision_code,
        "winning_clause": winner,
        "overridden_clauses": overridden,
        "missing_fields": [],
        "reason": f"Applied {winner['rule_clause_id']} (scope={winner['scope']}, priority={winner['priority']}).",
    }