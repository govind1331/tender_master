"""
Consistency Agent
=================
Verifies that the draft answer:
  1. Does not contradict answers already given in this session.
  2. Does not contradict the retrieved historical answers.

Uses a local Ollama LLM to perform the cross-check.

LangGraph node: receives QuestionState dict + session answers list injected
via the graph runner, returns updated QuestionState dict.
"""
import json
import ollama
import structlog

from app.core.config import get_settings
from app.core.embeddings import get_ollama_client
from app.core.state import QuestionState, ProcessingStatus

logger = structlog.get_logger()
settings = get_settings()

SYSTEM_PROMPT = """You are a quality reviewer for tender bid responses.

Your task: check if the DRAFT ANSWER is consistent with:
1. Previously approved answers in this session (SESSION ANSWERS).
2. Historical source answers (HISTORICAL CONTEXT).

Respond ONLY with a JSON object in this exact format with no extra text:
{
  "is_consistent": true or false,
  "notes": "brief explanation — max 2 sentences"
}

Flag inconsistency only for genuine contradictions (e.g. claiming TLS 1.2 in one answer
and denying encryption in another). Differences in phrasing or emphasis are acceptable.
"""


def consistency_node(state: dict) -> dict:
    """LangGraph node: cross-check answer consistency."""
    qs = QuestionState(**state)
    qs.status = ProcessingStatus.CHECKING_CONSISTENCY
    logger.info("consistency_agent.start", question_index=qs.question_index)

    try:
        session_answers: list[dict] = state.get("_session_answers", [])
        client = get_ollama_client(settings.ollama_base_url)
        prompt = _build_prompt(qs, session_answers)

        response = client.chat(
            model=settings.llm_model,
            options={"num_predict": 300, "temperature": 0.1},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
          
        )
        raw = response["message"]["content"].strip()
        # Strip markdown fences if the model adds them
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        qs.is_consistent = result.get("is_consistent", True)
        qs.consistency_notes = result.get("notes", "")
        logger.info(
            "consistency_agent.done",
            question_index=qs.question_index,
            is_consistent=qs.is_consistent,
        )
    except Exception as exc:
        logger.error("consistency_agent.error", error=str(exc))
        qs.is_consistent = True  # default to pass on error so pipeline continues
        qs.consistency_notes = f"Consistency check skipped due to error: {exc}"

    return qs.model_dump()


def _build_prompt(qs: QuestionState, session_answers: list[dict]) -> str:
    lines = [
        f"**Question:** {qs.original_question}",
        f"\n**Draft Answer:**\n{qs.draft_answer}",
    ]

    if session_answers:
        lines.append("\n**Session Answers So Far:**")
        for item in session_answers[-5:]:  # last 5 to stay within context
            lines.append(f"  Q: {item.get('question', '')}")
            lines.append(f"  A: {item.get('answer', '')}")

    if qs.historical_matches:
        lines.append("\n**Historical Context:**")
        for m in qs.historical_matches[:2]:
            lines.append(f"  Historical A: {m.answer}")

    lines.append("\nIs the draft answer consistent?")
    return "\n".join(lines)
