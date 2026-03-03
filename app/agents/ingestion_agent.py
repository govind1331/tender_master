"""
Ingestion Agent
===============
Reads historical QA Excel/JSON, generates embeddings for each Q+A pair
using a local HuggingFace sentence-transformer (no API key needed),
then stores metadata in PostgreSQL and vectors in pgvector.
"""
import hashlib
import json
from pathlib import Path
from typing import Optional
import pandas as pd
import structlog

from app.core.config import get_settings
from app.core.embeddings import embed_batch
from app.db.session import get_db
from app.db.models import HistoricalChunk, ChunkEmbedding

logger = structlog.get_logger()
settings = get_settings()


# ── File parsers ──────────────────────────────────────────────────────────────

def _parse_excel(filepath: str) -> list[dict]:
    """Parse Excel with columns: QUESTION, ANSWER, DOMAIN, SOURCE_FILE."""
    df = pd.read_excel(filepath)
    df.columns = [c.strip().upper() for c in df.columns]
    required = {"QUESTION", "ANSWER", "DOMAIN"}
    if not required.issubset(set(df.columns)):
        raise ValueError(f"Excel missing required columns: {required - set(df.columns)}")
    records = []
    for _, row in df.iterrows():
        q = str(row.get("QUESTION", "")).strip()
        a = str(row.get("ANSWER", "")).strip()
        d = str(row.get("DOMAIN", "General")).strip()
        src = str(row.get("SOURCE_FILE", Path(filepath).name)).strip()
        if q and a:
            records.append({"question": q, "answer": a, "domain": d, "source_file": src})
    return records


def _parse_json(filepath: str) -> list[dict]:
    with open(filepath) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    raise ValueError("JSON must be a list of {question, answer, domain} objects")


def _parse_file(filepath: str) -> list[dict]:
    ext = Path(filepath).suffix.lower()
    if ext in (".xlsx", ".xls"):
        return _parse_excel(filepath)
    elif ext == ".json":
        return _parse_json(filepath)
    raise ValueError(f"Unsupported file type: {ext}")


# ── Core ingestion logic ──────────────────────────────────────────────────────

def run_ingestion(filepath: str, source_filename: Optional[str] = None) -> dict:
    """
    Main entry point called by the Celery task.
    Embeds all new Q+A pairs in a single batch call for efficiency,
    then persists to PostgreSQL + pgvector.
    """
    source_filename = source_filename or Path(filepath).name
    logger.info("ingestion.start", file=source_filename)

    records = _parse_file(filepath)
    logger.info("ingestion.parsed", count=len(records))

    # Identify new records (idempotency check)
    new_records = []
    with get_db() as db:
        for rec in records:
            chunk_text = f"Question: {rec['question']}\nAnswer: {rec['answer']}"
            chunk_id = hashlib.sha256(chunk_text.encode()).hexdigest()[:64]
            if not db.get(HistoricalChunk, chunk_id):
                new_records.append({**rec, "chunk_text": chunk_text, "chunk_id": chunk_id})

    skipped = len(records) - len(new_records)
    if not new_records:
        logger.info("ingestion.all_duplicates", skipped=skipped)
        return {
            "status": "completed",
            "source_filename": source_filename,
            "total_records": len(records),
            "inserted": 0,
            "skipped_duplicates": skipped,
        }

    # Batch-embed all new chunks in one pass (efficient for CPU inference)
    logger.info("ingestion.embedding", count=len(new_records))
    texts = [r["chunk_text"] for r in new_records]
    vectors = embed_batch(texts, model_name=settings.embedding_model)

    # Persist metadata + vectors
    with get_db() as db:
        for rec, vector in zip(new_records, vectors):
            chunk = HistoricalChunk(
                id=rec["chunk_id"],
                source_file=rec["source_file"],
                domain=rec["domain"],
                original_question=rec["question"],
                original_answer=rec["answer"],
                chunk_text=rec["chunk_text"],
            )
            db.add(chunk)
            emb = ChunkEmbedding(
                id=rec["chunk_id"],
                chunk_id=rec["chunk_id"],
                embedding=vector,
            )
            db.add(emb)

    inserted = len(new_records)
    logger.info("ingestion.complete", inserted=inserted, skipped=skipped)
    return {
        "status": "completed",
        "source_filename": source_filename,
        "total_records": len(records),
        "inserted": inserted,
        "skipped_duplicates": skipped,
    }
