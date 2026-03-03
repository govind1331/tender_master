"""
Integration tests for FastAPI endpoints.
Run: pytest tests/ -v

Tests use TestClient (sync) with a mocked Celery to avoid needing
a running broker or LLM during CI.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import openpyxl
import io

from app.main import app

client = TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_xlsx_bytes(questions: list[str]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["QUESTION"])
    for q in questions:
        ws.append([q])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_historical_xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["QUESTION", "ANSWER", "DOMAIN", "SOURCE_FILE"])
    ws.append(["Do you support TLS?", "Yes, TLS 1.3", "Security", "test.xlsx"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Health ────────────────────────────────────────────────────────────────────

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ── Ingestion ─────────────────────────────────────────────────────────────────

@patch("app.api.routes_ingest.ingest_historical_task")
def test_ingest_accepts_xlsx(mock_task):
    mock_async = MagicMock()
    mock_async.id = "test-task-123"
    mock_task.apply_async.return_value = mock_async

    content = _make_historical_xlsx_bytes()
    response = client.post(
        "/api/v1/historical/ingest",
        files={"file": ("historical.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "test-task-123"
    assert data["status"] == "accepted"


def test_ingest_rejects_unsupported_format():
    response = client.post(
        "/api/v1/historical/ingest",
        files={"file": ("data.csv", b"q,a,domain\n", "text/csv")},
    )
    assert response.status_code == 400


def test_ingest_rejects_empty_file():
    response = client.post(
        "/api/v1/historical/ingest",
        files={"file": ("empty.xlsx", b"", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 400


# ── Tender Processing ─────────────────────────────────────────────────────────

@patch("app.api.routes_tender.process_tender_task")
def test_process_tender_accepted(mock_task):
    mock_async = MagicMock()
    mock_async.id = "tender-task-456"
    mock_task.apply_async.return_value = mock_async

    content = _make_xlsx_bytes(["Do you use encryption?", "What is your DR plan?"])
    response = client.post(
        "/api/v1/tender/process",
        files={"file": ("tender.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "tender-task-456"
    assert data["status"] == "accepted"


def test_process_tender_rejects_non_excel():
    response = client.post(
        "/api/v1/tender/process",
        files={"file": ("tender.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 400


# ── Job Status ────────────────────────────────────────────────────────────────

@patch("app.api.routes_tender.AsyncResult")
def test_job_status_pending(mock_result_cls):
    mock_result = MagicMock()
    mock_result.state = "PENDING"
    mock_result.info = {}
    mock_result_cls.return_value = mock_result

    response = client.get("/api/v1/jobs/some-task-id")
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"


@patch("app.api.routes_tender.AsyncResult")
def test_job_status_success(mock_result_cls):
    mock_result = MagicMock()
    mock_result.state = "SUCCESS"
    mock_result.result = {"session_id": "s1", "total_questions": 5}
    mock_result_cls.return_value = mock_result

    response = client.get("/api/v1/jobs/done-task-id")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert "result" in data
