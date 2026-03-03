"""
Routes: Tender Processing
POST /api/v1/tender/process       — upload new tender Excel, dispatches chord
GET  /api/v1/jobs/{task_id}       — poll dispatch task status (returns session_id)
GET  /api/v1/sessions/{session_id} — poll full session result from DB
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from celery.result import AsyncResult

from app.workers.tasks import dispatch_tender_task
from app.workers.celery_app import celery_app
from app.db.session import get_db
from app.db.models import TenderSession
import structlog
import uuid

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["Tender"])


@router.post("/tender/process")
async def process_tender(file: UploadFile = File(...)):
    """
    Upload a new tender questionnaire (.xlsx).
    Dispatches a parallel Celery chord — one agent chain per question.
    Returns immediately with a task_id and session_id to poll.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("xlsx", "xls"):
        raise HTTPException(status_code=400, detail="Supported formats: xlsx, xls")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")

    
    manual_session_id = str(uuid.uuid4())
    task = dispatch_tender_task.apply_async(
        kwargs={"file_bytes": content, "filename": file.filename, "session_id": manual_session_id}
    )
    logger.info("tender.dispatched", task_id=task.id, filename=file.filename, session_id=manual_session_id)
    return {
        "task_id": task.id,
        "session_id": manual_session_id,
        "status": "accepted",
        "message": f"Tender '{file.filename}' accepted. Poll /api/v1/jobs/{task.id} for session_id, then /api/v1/sessions/<session_id> for results.",
    }


@router.get("/jobs/{task_id}")
def get_job_status(task_id: str):
    """
    Poll the Celery task status of the dispatch task.
    Once SUCCESS, the response includes the session_id to use for full result polling.
    """
    result: AsyncResult = AsyncResult(task_id, app=celery_app)
    response: dict = {"task_id": task_id, "status": result.state}

    if result.state == "PENDING":
        response["message"] = "Job queued."
    elif result.state == "STARTED":
        response["message"] = "Dispatching questions to agent workers."
        response["meta"] = result.info or {}
    elif result.state == "SUCCESS":
        response["dispatch_result"] = result.result
        if isinstance(result.result, dict):
            response["session_id"] = result.result.get("session_id")
            response["message"] = "Questions dispatched. Poll /api/v1/sessions/<session_id> for live results."
    elif result.state == "FAILURE":
        response["error"] = str(result.result)
    return response


@router.get("/sessions/{session_id}")
def get_session_result(session_id: str):
    """
    Poll the aggregated session result directly from the database.
    Status transitions: dispatched → completed / completed_with_flags / completed_with_errors / failed
    """
    with get_db() as db:
        record = db.get(TenderSession, session_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    return {
        "session_id": session_id,
        "source_filename": record.source_filename,
        "total_questions": record.total_questions,
        "completed_count": record.completed_count,
        "flagged_count": record.flagged_count,
        "overall_status": record.overall_status,
        "result": record.result_payload,   # None until aggregate_results_task completes
    }
