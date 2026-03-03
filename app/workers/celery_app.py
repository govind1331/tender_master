from celery import Celery
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "tender_system",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks", "app.workers.agent_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,            # re-queue on worker crash
    worker_prefetch_multiplier=1,   # one task at a time per worker (LLM calls are heavy)
    result_expires=3600 * 24,       # results live 24 hours
    # chord_unlock polling interval (seconds) — how fast chord callbacks fire
    result_chord_join_timeout=600,
    task_routes={
        # Orchestration tasks
        "app.workers.tasks.ingest_historical_task":  {"queue": "ingestion"},
        "app.workers.tasks.dispatch_tender_task":    {"queue": "orchestration"},
        "app.workers.tasks.aggregate_results_task":  {"queue": "orchestration"},
        # Per-agent tasks — each runs in the "agents" queue
        "app.workers.agent_tasks.retrieve_task":     {"queue": "agents"},
        "app.workers.agent_tasks.answer_task":       {"queue": "agents"},
        "app.workers.agent_tasks.consistency_task":  {"queue": "agents"},
        "app.workers.agent_tasks.risk_task":         {"queue": "agents"},
        "app.workers.agent_tasks.format_task":       {"queue": "agents"},
    },
)
