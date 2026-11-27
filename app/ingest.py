import json
from pathlib import Path
from psycopg.types.json import Jsonb
from app.db import init_pool, close_pool, get_db_cursor
from app.llm_client import get_embedding


def ingest_policies(json_path: str = "data/sops/policies.json") -> None:
    policies = json.loads(Path(json_path).read_text(encoding="utf-8"))

    init_pool()
    try:
        with get_db_cursor() as cur:
            for doc in policies:
                # 1. Insert SOP document header (preserve existing metadata)
                cur.execute(
                    """
                    INSERT INTO sop_documents (sop_code, title, category)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (sop_code) DO NOTHING
                    RETURNING id;
                    """,
                    (doc["sop_code"], doc["title"], doc["category"]),
                )
                row = cur.fetchone()
                if row:
                    sop_id = row["id"]
                else:
                    cur.execute(
                        "SELECT id FROM sop_documents WHERE sop_code = %s;",
                        (doc["sop_code"],),
                    )
                    sop_id = cur.fetchone()["id"]

                # 2. Embed and append each versioned rule clause (never overwrite history)
                for rule in doc["rules"]:
                    cur.execute(
                        """
                        SELECT id FROM rule_versions
                        WHERE sop_id = %s AND version_num = %s AND rule_clause_id = %s;
                        """,
                        (sop_id, rule["version_num"], rule["rule_clause_id"]),
                    )
                    if cur.fetchone():
                        print(f"Skipped (already preserved): {rule['rule_clause_id']}")
                        continue

                    text_to_embed = f"{doc['title']} ({doc['category']}): {rule['content']}"
                    embedding = get_embedding(text_to_embed, task_type="RETRIEVAL_DOCUMENT")
                    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"

                    decision_payload = {
                        **rule.get("decision_rule", {}),
                        "required_fields": rule.get("required_fields", []),
                    }

                    cur.execute(
                        """
                        INSERT INTO rule_versions
                            (sop_id, version_num, rule_clause_id, content, valid_from, valid_to,
                             embedding, scope, priority, conditions, decision_rule)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s)
                        ON CONFLICT (sop_id, version_num, rule_clause_id) DO NOTHING;
                        """,
                        (
                            sop_id,
                            rule["version_num"],
                            rule["rule_clause_id"],
                            rule["content"],
                            rule["valid_from"],
                            rule["valid_to"],
                            vec_str,
                            rule.get("scope", "GLOBAL"),
                            rule.get("priority", 10),
                            Jsonb(rule.get("conditions", {})),
                            Jsonb(decision_payload),
                        ),
                    )
                    print(f"Inserted new rule version: {rule['rule_clause_id']}")
    finally:
        close_pool()


if __name__ == "__main__":
    ingest_policies()
    print("Done! All historical & structured policy rules preserved in Neon.")