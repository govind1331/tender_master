"""
Agent Tasks
===========
Each agent in the pipeline is a proper Celery task.
They are composed into a per-question chain using Celery canvas:

    retrieve_task  →  answer_task  →  consistency_task  →  risk_task  →  format_task

All questions in a tender are dispatched as a parallel group (chord),
with aggregate_results_task as the callback once all questions complete.

State is passed forward as a plain dict (serialisable over Redis)
matching the QuestionState schema.
"""

import structlog
from celery import Task

from app.workers.celery_app import celery_app
from app.db.session import init_db
from app.core.state import QuestionState, ProcessingStatus

logger = structlog.get_logger()


# ── Base task: ensures DB is ready on first run ───────────────────────────────


class AgentBaseTask(Task):
    _db_init = False

    def __call__(self, *args, **kwargs):
        if not AgentBaseTask._db_init:
            init_db()
            AgentBaseTask._db_init = True
        return super().__call__(*args, **kwargs)


# ── Agent task definitions ────────────────────────────────────────────────────
@celery_app.task(
    bind=True,
    base=AgentBaseTask,
    name="app.workers.agent_tasks.process_question_task", # New consolidated task
    queue="agents",
    time_limit=300, # Increased for the full graph run
)
def process_question_task(self, initial_state_dict: dict, session_answers: list) -> dict:
    """
    Runs the ENTIRE LangGraph for a single question.
    Celery handles the distribution; LangGraph handles the agent flow.
    """
    from app.core.graph import get_compiled_graph
    
    # Inject the session answers for the consistency agent
    initial_state_dict["_session_answers"] = session_answers
    
    # Unique thread_id allows RedisSaver to checkpoint correctly in Redis
    thread_id = f"q_{initial_state_dict['question_index']}"
    config = {"configurable": {"thread_id": thread_id}}
    
    try:
        compiled_graph = get_compiled_graph()
        # This one call replaces the individual task chain logic
        final_state = compiled_graph.invoke(initial_state_dict, config=config)
        return final_state
    except Exception as exc:
        logger.error("graph.execution.failed", index=initial_state_dict.get("question_index"), error=str(exc))
        # Return a failure state so the aggregator can still process it
        initial_state_dict["status"] = "failed"
        initial_state_dict["error"] = str(exc)
        return initial_state_dict