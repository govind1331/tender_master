"""
LangGraph Orchestration Graph
==============================
Defines the per-question state machine and runs it for each question
in the tender.

Checkpointer: RedisSaver (langgraph-checkpoint-redis)
  - Replaces SqliteSaver which returned a _GeneratorContextManager and
    required `with` syntax incompatible with long-lived Celery workers.
  - RedisSaver.from_conn_string() returns a saver instance directly.
  - Uses the same Redis instance as Celery — no extra infrastructure.
  - Thread-safe: each question gets a unique thread_id key in Redis.

Graph flow (per question):
  retrieve → answer → consistency → risk → format → END
  (conditional short-circuit to format on any agent failure)

Session state is tracked in TenderSessionState (long-term memory).
"""
import os
from typing import Any
import structlog

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.redis import RedisSaver

from app.core.config import get_settings
from app.core.state import (
    QuestionState, TenderSessionState, ProcessingStatus
)
from app.agents.retriever_agent import retriever_node
from app.agents.answer_agent import answer_node
from app.agents.consistency_agent import consistency_node
from app.agents.risk_agent import risk_node
from app.agents.formatter_agent import formatter_node

logger = structlog.get_logger()
settings = get_settings()

# ── Compiled graph singleton — built once per worker process ──────────────────
_compiled_graph = None




def get_compiled_graph():
    """
    Return the compiled LangGraph graph.
    FIX: Added index initialization for RedisSearch.
    """
    global _compiled_graph
    if _compiled_graph is None:
       
        saver_context = RedisSaver.from_conn_string(settings.redis_url)
       
        checkpointer = saver_context.__enter__() 
        
        try:
            checkpointer.setup() 
            logger.info("langgraph.checkpointer_setup_complete")
        except Exception as e:
            
            logger.warning("langgraph.checkpointer_setup_warning", error=str(e))

        # 4. Compile the graph
        graph = _build_graph()
        _compiled_graph = graph.compile(checkpointer=checkpointer)
        
        logger.info("langgraph.compiled", checkpointer="RedisSaver")
    
    return _compiled_graph

# ── Conditional routing ───────────────────────────────────────────────────────

def _route_after_retrieval(state: dict) -> str:
    if QuestionState(**state).status == ProcessingStatus.FAILED:
        return "format"
    return "answer"


def _route_after_answer(state: dict) -> str:
    if QuestionState(**state).status == ProcessingStatus.FAILED:
        return "format"
    return "consistency"


def _route_after_consistency(state: dict) -> str:
    if QuestionState(**state).status == ProcessingStatus.FAILED:
        return "format"
    return "risk"


# ── Graph definition ──────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    graph = StateGraph(dict)

    graph.add_node("retrieve",    retriever_node)
    graph.add_node("answer",      answer_node)
    graph.add_node("consistency", consistency_node)
    graph.add_node("risk",        risk_node)
    graph.add_node("format",      formatter_node)

    graph.set_entry_point("retrieve")

    graph.add_conditional_edges(
        "retrieve", _route_after_retrieval,
        {"answer": "answer", "format": "format"}
    )
    graph.add_conditional_edges(
        "answer", _route_after_answer,
        {"consistency": "consistency", "format": "format"}
    )
    graph.add_conditional_edges(
        "consistency", _route_after_consistency,
        {"risk": "risk", "format": "format"}
    )
    graph.add_edge("risk",   "format")
    graph.add_edge("format", END)

    return graph


# ── Session runner ────────────────────────────────────────────────────────────

def run_tender_session(session: TenderSessionState) -> TenderSessionState:
    """
    Process all questions sequentially.
    Failure in one question never stops the rest.
    Each question's checkpoint is stored in Redis under a unique thread_id.
    """
    compiled = get_compiled_graph()
    logger.info("session.start", session_id=session.session_id, total=session.total_questions)

    for idx, question in enumerate(session.questions):
        # Unique Redis key per question — survives worker restarts mid-session
        thread_id = f"{session.session_id}:q{idx}"

        initial_state: dict[str, Any] = QuestionState(
            question_index=idx,
            original_question=question,
        ).model_dump()
        # Inject session answers for consistency cross-check (not a Pydantic field)
        initial_state["_session_answers"] = session.session_answers_so_far.copy()

        try:
            config = {"configurable": {"thread_id": thread_id}}
            final_state = compiled.invoke(initial_state, config=config)
            final_qs = QuestionState(**final_state)

            session.completed_questions[idx] = final_qs
            session.processed_count += 1

            if final_qs.is_flagged:
                session.flagged_count += 1

            # Append to long-term session memory for subsequent consistency checks
            session.session_answers_so_far.append({
                "question": question,
                "answer":   final_qs.draft_answer,
                "domain":   final_qs.domain,
            })
            logger.info("question.done", index=idx, flagged=final_qs.is_flagged)

        except Exception as exc:
            logger.error("question.failed", index=idx, error=str(exc))
            session.failed_questions.append(idx)

            failed_qs = QuestionState(
                question_index=idx,
                original_question=question,
                status=ProcessingStatus.FAILED,
                error=str(exc),
            )
            failed_qs.formatted_answer = {
                "question_index":      idx,
                "original_question":   question,
                "generated_answer":    "",
                "domain":              "",
                "confidence_level":    "low",
                "confidence_score":    0.0,
                "historical_alignment": {
                    "has_history": False,
                    "best_similarity_score": 0.0,
                    "matched_sources": [],
                },
                "consistency":     {"is_consistent": False, "notes": ""},
                "risk_assessment": {
                    "risk_level": "high",
                    "is_flagged": True,
                    "notes": "Processing failed",
                },
                "status": "failed",
                "error":  str(exc),
            }
            session.completed_questions[idx] = failed_qs

    # Determine overall status
    if len(session.failed_questions) == session.total_questions:
        session.overall_status = "failed"
    elif session.failed_questions:
        session.overall_status = "completed_with_errors"
    elif session.flagged_count > 0:
        session.overall_status = "completed_with_flags"
    else:
        session.overall_status = "completed"

    logger.info(
        "session.complete",
        session_id=session.session_id,
        status=session.overall_status,
        flagged=session.flagged_count,
        failed=len(session.failed_questions),
    )
    return session
