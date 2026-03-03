"""
Answer Agent
============
Generates an initial answer draft using a local Ollama LLM,
grounded in retrieved historical context when available.

LangGraph node: receives QuestionState dict, returns updated dict.
"""
import ollama
import structlog

from app.core.config import get_settings
from app.core.embeddings import get_ollama_client
from app.core.state import QuestionState, ProcessingStatus

logger = structlog.get_logger()
settings = get_settings()

SYSTEM_PROMPT = """You are a professional bid writer for a software company responding to tender questionnaires.

Rules you MUST follow:
1. Base your answer strictly on the historical context provided. Do not invent certifications, SLAs, or capabilities not mentioned in context.
2. Adapt tone and phrasing to match the question's wording while preserving factual accuracy.
3. Be concise and professional. Avoid bullet points unless the question explicitly asks for a list.
4. If no historical context is provided, give a generic but honest response that avoids overcommitting.
5. Never fabricate specific certification numbers, dates, or vendor names.
"""


def answer_node(state: dict) -> dict:
    """LangGraph node: generate draft answer."""
    qs = QuestionState(**state)
    qs.status = ProcessingStatus.ANSWERING
    logger.info("answer_agent.start", question_index=qs.question_index)

    try:
        client = get_ollama_client(settings.ollama_base_url)
        prompt = _build_prompt(qs)
        response = client.chat(
            model=settings.llm_model,
            options={"num_predict": 800, "temperature": 0.3},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            
        )
        qs.draft_answer = response["message"]["content"].strip()
        logger.info("answer_agent.done", question_index=qs.question_index)
    except Exception as exc:
        logger.error("answer_agent.error", error=str(exc))
        qs.draft_answer = ""
        qs.error = f"Answer agent error: {exc}"
        qs.status = ProcessingStatus.FAILED

    return qs.model_dump()


def _build_prompt(qs: QuestionState) -> str:
    lines = [f"**Tender Question:** {qs.original_question}\n"]

    if qs.has_history and qs.historical_matches:
        lines.append("**Relevant Historical Responses (for reference):**\n")
        for i, match in enumerate(qs.historical_matches[:3], 1):
            lines.append(
                f"[{i}] Domain: {match.domain} | Similarity: {match.similarity_score:.2f}\n"
                f"  Q: {match.question}\n"
                f"  A: {match.answer}\n"
            )
        lines.append(
            "\nUsing the historical responses above as your primary reference, "
            "write a professional answer to the tender question. "
            "Adapt the wording to match the question precisely."
        )
    else:
        lines.append(
            "No historical responses are available for this question. "
            "Write a professional, honest answer without inventing specific claims."
        )

    return "\n".join(lines)
