"""
Routes: Historical Ingestion
POST /api/v1/historical/ingest   — upload historical QA file
GET  /api/v1/historical/status   — count chunks stored
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.workers.tasks import ingest_historical_task
from app.db.session import get_db
from app.db.models import HistoricalChunk
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/historical", tags=["Historical"])


@router.post("/ingest")
async def ingest_historical(file: UploadFile = File(...)):
    """
    Upload a historical QA Excel (.xlsx) or JSON file.
    The file is queued for async RAG ingestion.

    **Excel format expected:**
    | QUESTION | ANSWER | DOMAIN | SOURCE_FILE |
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("xlsx", "xls", "json"):
        raise HTTPException(status_code=400, detail="Supported formats: xlsx, xls, json")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="File is empty")

    task = ingest_historical_task.apply_async(
        kwargs={"file_bytes": content, "filename": file.filename}
    )
    logger.info("ingest.queued", task_id=task.id, filename=file.filename)

    return {
        "task_id": task.id,
        "status": "accepted",
        "message": f"Ingestion of '{file.filename}' queued. Use /api/v1/jobs/{task.id} to track.",
    }


@router.get("/status")
def historical_status():
    """Return count of stored historical chunks."""
    with get_db() as db:
        count = db.query(HistoricalChunk).count()
    return {"stored_chunks": count}
