"""
Formatter Agent
===============
Transforms the processed QuestionState into the final JSON structure
as defined in the problem statement output spec.

LangGraph node: receives QuestionState dict, returns updated dict with
formatted_answer populated.
"""
import structlog
from app.core.state import QuestionState, ProcessingStatus

logger = structlog.get_logger()


def formatter_node(state: dict) -> dict:
    """LangGraph node: format final answer."""
    qs = QuestionState(**state)
    qs.status = ProcessingStatus.FORMATTING

    try:
        # Confidence score: blend similarity + consistency + risk
        confidence_score = _compute_confidence(qs)

        qs.formatted_answer = {
            "question_index": qs.question_index,
            "original_question": qs.original_question,
            "generated_answer": qs.draft_answer,
            "domain": qs.domain or _infer_domain(qs),
            "confidence_level": _confidence_label(confidence_score),
            "confidence_score": round(confidence_score, 2),
            "historical_alignment": {
                "has_history": qs.has_history,
                "best_similarity_score": round(qs.best_similarity, 4),
                "matched_sources": [
                    {
                        "source_file": m.source_file,
                        "domain": m.domain,
                        "similarity": round(m.similarity_score, 4),
                    }
                    for m in qs.historical_matches[:3]
                ],
            },
            "consistency": {
                "is_consistent": qs.is_consistent,
                "notes": qs.consistency_notes,
            },
            "risk_assessment": {
                "risk_level": qs.risk_level.value,
                "is_flagged": qs.is_flagged,
                "notes": qs.risk_notes,
            },
            "status": "completed" if not qs.error else "failed",
            "error": qs.error,
        }
        qs.status = ProcessingStatus.COMPLETED
        logger.info("formatter.done", question_index=qs.question_index)
    except Exception as exc:
        logger.error("formatter.error", error=str(exc))
        qs.error = str(exc)
        qs.status = ProcessingStatus.FAILED

    return qs.model_dump()


def _compute_confidence(qs: QuestionState) -> float:
    """Heuristic confidence: 0.0 – 1.0."""
    score = 0.5  # baseline
    if qs.has_history:
        score += qs.best_similarity * 0.3
    if qs.is_consistent:
        score += 0.1
    risk_penalty = {"low": 0.0, "medium": -0.1, "high": -0.3}
    score += risk_penalty.get(qs.risk_level.value, 0.0)
    return max(0.0, min(1.0, score))


def _confidence_label(score: float) -> str:
    if score >= 0.85:
        return "high"
    elif score >= 0.65:
        return "medium"
    return "low"


def _infer_domain(qs: QuestionState) -> str:
    """Infer domain from top historical match if not set."""
    if qs.historical_matches:
        return qs.historical_matches[0].domain
    return "General"
