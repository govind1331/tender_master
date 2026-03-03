"""
Orchestration Tasks (Refactored)
================================
1. ingest_historical_task : RAG ingestion.
2. dispatch_tender_task   : Parses Excel, then fans out ONE task per question.
                            Each task invokes the full LangGraph.
3. aggregate_results_task  : Collects final states from all LangGraph runs.

CHANGE: Removed Celery chains. We now pass the entire "Micro-Orchestration" 
responsibility to LangGraph within a single Celery task.
"""
import uuid
import os
import tempfile
import structlog
import pandas as pd
from celery import Task, chord, group

from app.workers.celery_app import celery_app
# CHANGE: Import the new consolidated agent task
from app.workers.agent_tasks import process_question_task
from app.agents.ingestion_agent import run_ingestion
from app.core.state import QuestionState, ProcessingStatus
from app.db.session import get_db, init_db
from app.db.models import TenderSession

logger = structlog.get_logger()

class BaseTask(Task):
    _db_init = False

    def __call__(self, *args, **kwargs):
        if not BaseTask._db_init:
            init_db()
            BaseTask._db_init = True
        return super().__call__(*args, **kwargs)

# --- 1. Ingestion task (unchanged) ---

@celery_app.task(
    bind=True,
    base=BaseTask,
    name="app.workers.tasks.ingest_historical_task",
    max_retries=3,
    default_retry_delay=10,
    queue="ingestion",
)
def ingest_historical_task(self, file_bytes: bytes, filename: str) -> dict:
    tmp_path = None
    try:
        self.update_state(state="STARTED", meta={"filename": filename})
        suffix = os.path.splitext(filename)[1]
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        return run_ingestion(tmp_path, source_filename=filename)
    except Exception as exc:
        logger.error("ingest_task.error", error=str(exc))
        raise self.retry(exc=exc)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

# --- 2. Dispatcher (Refactored) ---

@celery_app.task(
    bind=True,
    base=BaseTask,
    name="app.workers.tasks.dispatch_tender_task",
    max_retries=1,
    queue="orchestration",
    time_limit=60,
)
def dispatch_tender_task(self, file_bytes: bytes, filename: str, session_id: str = None) -> dict:
    """
    CHANGE: This task now dispatches a GROUP of LangGraph runs.
    It no longer builds complex Celery chains.
    """
    tmp_path = None
    session_id = session_id or str(uuid.uuid4())
    task_id = self.request.id

    try:
        self.update_state(state="STARTED", meta={"session_id": session_id})

        suffix = os.path.splitext(filename)[1]
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        questions = _parse_tender_excel(tmp_path)
        logger.info("dispatch.parsed", count=len(questions), session_id=session_id)

        _save_session(session_id, task_id, filename, len(questions))

        # CHANGE: We define a single signature per question.
        # Each signature calls process_question_task which executes the 
        # entire LangGraph (retrieve -> answer -> risk -> etc.).
        tasks_to_run = []
        for idx, question in enumerate(questions):
            initial_state = QuestionState(
                question_index=idx,
                original_question=question,
            ).model_dump()

            # Note: session_answers remains empty for parallel runs to avoid 
            # race conditions. Global consistency is best checked in the aggregator.
            tasks_to_run.append(
                process_question_task.s(initial_state, session_answers=[])
            )

        # CHANGE: Simple chord structure.
        # Header: list of LangGraph runs (parallel)
        # Callback: Single aggregator (serial)
        workflow = chord(
            group(*tasks_to_run),
            aggregate_results_task.s(
                session_id=session_id,
                filename=filename,
                total=len(questions),
            )
        )
        workflow.apply_async()

        logger.info("dispatch.chord_dispatched", session_id=session_id, questions=len(questions))
        return {
            "session_id": session_id,
            "status": "dispatched",
            "total_questions": len(questions),
            "message": f"Processing {len(questions)} questions in parallel via LangGraph. Poll /api/v1/sessions/{session_id}",
        }

    except Exception as exc:
        logger.error("dispatch.error", error=str(exc))
        raise self.retry(exc=exc)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

# --- 3. Aggregator (Minor Logic Cleanup) ---

@celery_app.task(
    bind=True,
    base=BaseTask,
    name="app.workers.tasks.aggregate_results_task",
    queue="orchestration",
    time_limit=120,
)
def aggregate_results_task(self, results: list, session_id: str, filename: str, total: int) -> dict:
    """
    Receives list of final QuestionState dicts from all LangGraph runs.
    """
    logger.info("aggregate.start", session_id=session_id, result_count=len(results))

    answers = []
    flagged_count = 0
    failed_count = 0

    for state in results:
        if not state:
            failed_count += 1
            continue
            
        qs = QuestionState(**state)
        
        # Check if the LangGraph final state indicates failure
        if qs.status == ProcessingStatus.FAILED:
            failed_count += 1
        if qs.is_flagged:
            flagged_count += 1
            
        # Collect the formatted answer generated by LangGraph's formatter_node
        answers.append(qs.formatted_answer if qs.formatted_answer else {
            "question_index": qs.question_index,
            "original_question": qs.original_question,
            "status": "failed",
            "error": qs.error or "Unknown failure in LangGraph execution",
        })

    answers.sort(key=lambda a: a.get("question_index", 0))

    if failed_count == total:
        overall_status = "failed"
    elif failed_count > 0:
        overall_status = "completed_with_errors"
    elif flagged_count > 0:
        overall_status = "completed_with_flags"
    else:
        overall_status = "completed"

    summary = {
        "session_id": session_id,
        "source_filename": filename,
        "total_questions": total,
        "completed": total - failed_count,
        "failed": failed_count,
        "flagged_count": flagged_count,
        "overall_status": overall_status,
        "answers": answers,
    }

    _update_session(session_id, total - failed_count, flagged_count, overall_status, summary)
    return summary

# --- Helpers (Unchanged) ---

def _parse_tender_excel(filepath: str) -> list[str]:
    df = pd.read_excel(filepath)
    df.columns = [c.strip().upper() for c in df.columns]
    if "QUESTION" not in df.columns:
        df = df.rename(columns={df.columns[0]: "QUESTION"})
    questions = df["QUESTION"].dropna().astype(str).str.strip().tolist()
    return [q for q in questions if q]

def _save_session(session_id: str, task_id: str, filename: str, total: int):
    with get_db() as db:
        record = TenderSession(
            id=session_id,
            task_id=task_id,
            source_filename=filename,
            total_questions=total,
            overall_status="in_progress",
        )
        db.merge(record)

def _update_session(session_id: str, completed: int, flagged: int, status: str, summary: dict):
    with get_db() as db:
        record = db.get(TenderSession, session_id)
        if record:
            record.completed_count = completed
            record.flagged_count = flagged
            record.overall_status = status
            record.result_payload = summary
            db.merge(record)