from datetime import date
from app.db import get_db_cursor
from app.llm_client import get_embedding


def retrieve_applicable_clauses(
    query_text: str,
    incident_date: date,
    top_k: int = 5,
    ignore_dates: bool = False,
) -> list[dict]:
    """
    Retrieves policy clauses using:
    1. Temporal filtering (valid_from <= incident_date <= valid_to)
    2. Hybrid search: pgvector semantic similarity + Postgres full-text keyword search
    3. Reciprocal Rank Fusion (RRF) scoring
    """
    query_vec = get_embedding(query_text, task_type="RETRIEVAL_QUERY")
    vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"

    # Toggle date filter off ONLY when benchmarking naive baseline RAG in evals
    date_filter_sql = "" if ignore_dates else """
        AND rv.valid_from <= %(incident_date)s
        AND (rv.valid_to IS NULL OR rv.valid_to >= %(incident_date)s)
    """

    sql = f"""
    WITH eligible_rules AS (
        SELECT
            rv.id,
            sd.sop_code,
            sd.title AS sop_title,
            sd.category,
            rv.version_num,
            rv.rule_clause_id,
            rv.content,
            rv.valid_from,
            rv.valid_to,
            rv.scope,
            rv.priority,
            rv.conditions,
            rv.decision_rule,
            rv.embedding <=> %(query_vec)s::vector AS cosine_distance,
            ts_rank_cd(rv.tsv, plainto_tsquery('english', %(query_text)s)) AS kw_score
        FROM rule_versions rv
        JOIN sop_documents sd ON rv.sop_id = sd.id
        WHERE 1=1
        {date_filter_sql}
    ),
    ranked AS (
        SELECT
            *,
            ROW_NUMBER() OVER (ORDER BY cosine_distance ASC) AS vec_rank,
            ROW_NUMBER() OVER (ORDER BY kw_score DESC, cosine_distance ASC) AS kw_rank
        FROM eligible_rules
    )
    SELECT
        id,
        sop_code,
        sop_title,
        category,
        version_num,
        rule_clause_id,
        content,
        valid_from,
        valid_to,
        scope,
        priority,
        conditions,
        decision_rule,
        ROUND(kw_score::numeric, 4) AS kw_score,
        ROUND((1.0 - cosine_distance)::numeric, 4) AS semantic_similarity,
        ROUND((1.0 / (60 + vec_rank) + 1.0 / (60 + kw_rank))::numeric, 4) AS rrf_score
    FROM ranked
    ORDER BY rrf_score DESC
    LIMIT %(top_k)s;
    """

    with get_db_cursor() as cur:
        cur.execute(
            sql,
            {
                "query_vec": vec_str,
                "query_text": query_text,
                "incident_date": incident_date,
                "top_k": top_k,
            },
        )
        return list(cur.fetchall())