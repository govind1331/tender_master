"""
Retriever Agent
===============
For a given question, performs semantic similarity search against the
pgvector store. If best match ≥ SIMILARITY_THRESHOLD, marks question
as having history and returns top matches.

Embeddings are generated locally via HuggingFace sentence-transformers.

Called as a LangGraph node — receives and returns QuestionState dict.
"""
import structlog
from sqlalchemy import text

from app.core.config import get_settings
from app.core.embeddings import embed_text
from app.core.state import QuestionState, HistoricalMatch, ProcessingStatus
from app.db.session import get_db

logger = structlog.get_logger()
settings = get_settings()

TOP_K = 5  # retrieve top-5 candidates, filter by threshold


def retriever_node(state: dict) -> dict:
    """LangGraph node: semantic retrieval for one question."""
    qs = QuestionState(**state)
    qs.status = ProcessingStatus.RETRIEVING
    logger.info("retriever.start", question_index=qs.question_index)

    try:
        query_vec = embed_text(qs.original_question, model_name=settings.embedding_model)
        matches = _similarity_search(query_vec, top_k=TOP_K)

        if matches:
            best = matches[0].similarity_score
            qs.best_similarity = best
            qs.has_history = best >= settings.similarity_threshold
            qs.historical_matches = matches if qs.has_history else []
        else:
            qs.has_history = False
            qs.best_similarity = 0.0

        logger.info(
            "retriever.done",
            question_index=qs.question_index,
            has_history=qs.has_history,
            best_score=qs.best_similarity,
        )
    except Exception as exc:
        logger.error("retriever.error", error=str(exc))
        qs.error = f"Retriever error: {exc}"
        qs.status = ProcessingStatus.FAILED

    return qs.model_dump()


def _similarity_search(query_vec: list[float], top_k: int = 5) -> list[HistoricalMatch]:
    """Raw cosine similarity search using pgvector <=> operator."""
    vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

    sql = text("""
        SELECT
            hc.id,
            hc.original_question,
            hc.original_answer,
            hc.domain,
            hc.source_file,
            1 - (ce.embedding <=> CAST(:vec AS vector)) AS similarity
        FROM chunk_embeddings ce
        JOIN historical_chunks hc ON ce.chunk_id = hc.id
        ORDER BY ce.embedding <=> CAST(:vec AS vector)
        LIMIT :top_k
    """)

    results = []
    with get_db() as db:
        rows = db.execute(sql, {"vec": vec_str, "top_k": top_k}).fetchall()
        for row in rows:
            results.append(HistoricalMatch(
                chunk_id=row.id,
                question=row.original_question,
                answer=row.original_answer,
                domain=row.domain,
                similarity_score=float(row.similarity),
                source_file=row.source_file,
            ))
    return results
