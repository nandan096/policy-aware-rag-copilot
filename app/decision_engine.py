from typing import Any


def _condition_fact_key(condition_key: str) -> str:
    """Maps condition schema keys to incident fact field names."""
    return "parcel_value" if condition_key == "min_value" else condition_key


def _check_clause_conditions(conditions: dict[str, Any], facts: dict[str, Any]) -> tuple[bool, bool]:
    """
    Checks whether incident facts satisfy a clause's conditions.
    Returns (is_relevant_candidate, is_fully_satisfied):
    - is_relevant_candidate: True if issue_type matches (when provided) and no fact contradicts.
    - is_fully_satisfied: True if all required condition keys exist in facts and pass.
    """
    if not conditions or "issue_type" not in conditions:
        return False, False

    # If caller provided an issue_type and it doesn't match this clause, reject immediately
    if facts.get("issue_type") is not None:
        if str(facts["issue_type"]).strip().lower() != str(conditions["issue_type"]).strip().lower():
            return False, False

    is_fully_satisfied = True

    for key, expected in conditions.items():
        if key == "min_value":
            actual_val = facts.get("parcel_value")
            if actual_val is None:
                is_fully_satisfied = False
                continue
            if float(actual_val) < float(expected):
                return False, False
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


def _derive_required_fields(clause: dict[str, Any]) -> list[str]:
    """Combines explicit rule required_fields with fields implied by clause conditions."""
    rule_fields = list((clause.get("decision_rule") or {}).get("required_fields", []))
    cond_fields = [_condition_fact_key(k) for k in (clause.get("conditions") or {}).keys()]
    return list(dict.fromkeys(rule_fields + cond_fields))


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
    # Gate 1: Abstain if retrieval found nothing or best semantic match is too weak
    if not retrieved_clauses:
        return {
            "status": "ABSTAIN",
            "decision_code": "ABSTAIN_NO_APPLICABLE_POLICY",
            "winning_clause": None,
            "overridden_clauses": [],
            "missing_fields": [],
            "reason": "No active SOP policies found for this incident date.",
        }

    max_similarity = max(float(c.get("semantic_similarity", 0.0)) for c in retrieved_clauses)
    if max_similarity < min_similarity_threshold:
        return {
            "status": "ABSTAIN",
            "decision_code": "ABSTAIN_OUT_OF_SCOPE",
            "winning_clause": None,
            "overridden_clauses": [],
            "missing_fields": [],
            "reason": f"Query falls outside documented SOP coverage (max similarity {max_similarity} < {min_similarity_threshold}).",
        }

    # When no structured facts are provided at all, require high semantic confidence
    if not incident_facts and max_similarity < 0.76:
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

    fully_matched.sort(key=lambda c: int(c.get("priority", 10)), reverse=True)
    partial_candidates.sort(key=lambda c: int(c.get("priority", 10)), reverse=True)

    # Gate 3: Check if a higher-priority override could apply, or no rule matched because fields are missing
    highest_matched_priority = int(fully_matched[0]["priority"]) if fully_matched else -1

    for candidate in partial_candidates:
        if int(candidate.get("priority", 10)) > highest_matched_priority:
            req_fields = _derive_required_fields(candidate)
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

    # Gate 4: Resolve Precedence (highest priority wins; detect equal-priority conflicts)
    winner = fully_matched[0]
    overridden = fully_matched[1:]
    top_priority = int(winner.get("priority", 10))

    # Gate 5: Check required action fields on the winning rule
    winner_rule = winner.get("decision_rule") or {}
    missing_action_fields = [
        f for f in _derive_required_fields(winner) if incident_facts.get(f) is None
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

    # Check if another matched clause shares the exact top priority but produces a conflicting decision
    for peer in overridden:
        if int(peer.get("priority", 10)) == top_priority:
            peer_rule = peer.get("decision_rule") or {}
            peer_missing = [f for f in _derive_required_fields(peer) if incident_facts.get(f) is None]
            if not peer_missing:
                peer_code = _execute_decision_rule(peer_rule, incident_facts)
                if peer_code != decision_code:
                    return {
                        "status": "NEEDS_REVIEW",
                        "decision_code": "CONFLICTING_EQUAL_PRIORITY_RULES",
                        "winning_clause": winner,
                        "overridden_clauses": overridden,
                        "missing_fields": [],
                        "reason": (
                            f"Equal-priority conflict (priority={top_priority}) between "
                            f"{winner['rule_clause_id']} ({decision_code}) and "
                            f"{peer['rule_clause_id']} ({peer_code})."
                        ),
                    }

    return {
        "status": "DECIDED",
        "decision_code": decision_code,
        "winning_clause": winner,
        "overridden_clauses": overridden,
        "missing_fields": [],
        "reason": f"Applied {winner['rule_clause_id']} (scope={winner['scope']}, priority={winner['priority']}).",
    }