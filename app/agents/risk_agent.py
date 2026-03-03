"""
Risk Agent
==========
Reviews the draft answer and assigns a risk level:
  LOW    — answer is well-supported and accurate
  MEDIUM — minor gaps or assumptions present
  HIGH   — potential legal/compliance exposure or unverifiable claims

Uses a local Ollama LLM for assessment.

LangGraph node: receives QuestionState dict, returns updated dict.
"""
import json
import ollama
import structlog

from app.core.config import get_settings
from app.core.embeddings import get_ollama_client
from app.core.state import QuestionState, ProcessingStatus, RiskLevel

logger = structlog.get_logger()
settings = get_settings()

SYSTEM_PROMPT = """You are a risk reviewer for tender bid responses at a software company.

Evaluate the answer for:
- Unsupported claims (certifications, SLAs, capabilities not evidenced)
- Legal or compliance overcommitment
- Vague answers that could be interpreted against us
- Contradictions with industry norms

Respond ONLY with a JSON object in this exact format with no extra text:
{
  "risk_level": "low" or "medium" or "high",
  "is_flagged": true or false,
  "notes": "brief explanation — max 2 sentences"
}

Flag (is_flagged: true) only for medium or high risk.
"""


def risk_node(state: dict) -> dict:
    """LangGraph node: assess answer risk."""
    qs = QuestionState(**state)
    qs.status = ProcessingStatus.ASSESSING_RISK
    logger.info("risk_agent.start", question_index=qs.question_index)

    try:
        prompt = (
            f"**Question:** {qs.original_question}\n\n"
            f"**Answer:** {qs.draft_answer}\n\n"
            f"**Consistency note:** {qs.consistency_notes or 'None'}\n\n"
            "Assess the risk of this answer."
        )
        client = get_ollama_client(settings.ollama_base_url)
        response = client.chat(
            model=settings.llm_model,
            options={"num_predict": 300, "temperature": 0.1},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            
        )
        raw = response["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)

        qs.risk_level = RiskLevel(result.get("risk_level", "low"))
        qs.is_flagged = result.get("is_flagged", False)
        qs.risk_notes = result.get("notes", "")

        logger.info(
            "risk_agent.done",
            question_index=qs.question_index,
            risk_level=qs.risk_level,
            is_flagged=qs.is_flagged,
        )
    except Exception as exc:
        logger.error("risk_agent.error", error=str(exc))
        qs.risk_level = RiskLevel.LOW
        qs.is_flagged = False
        qs.risk_notes = f"Risk assessment skipped: {exc}"

    return qs.model_dump()
