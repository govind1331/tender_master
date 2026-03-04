# Tender Master

A multi-agent pipeline that takes a tender questionnaire (Excel), runs each question through a chain of AI agents, and returns structured, risk-assessed answers grounded in your company's historical bid responses.

Built with FastAPI, LangGraph, Celery, PostgreSQL + pgvector, and Ollama.

---

## What it does

Responding to tenders is slow. You're answering the same categories of questions across every bid, manually hunting through past submissions to keep answers consistent, and hoping nothing you write creates a legal or compliance problem.

Tender Master automates that loop. Upload an Excel file containing your questionnaire, and the system:

1. Embeds each question and retrieves the most relevant historical answers from your knowledge base
2. Drafts a professional response grounded in that context — not the LLM's general knowledge
3. Checks the draft for internal contradictions against previous answers
4. Flags anything that looks like a compliance overcommitment or unsupported claim
5. Returns a confidence-scored JSON payload ready for human review

Nothing leaves your infrastructure. Embeddings run via a local HuggingFace sentence-transformer. Generation runs via Ollama.

---

## Architecture

Two orchestration layers work together:

**Celery** handles distribution. When a tender file comes in, it's parsed and each question is dispatched as an independent parallel task across the worker pool.

**LangGraph** handles agent logic. Each Celery worker runs a self-contained LangGraph state machine for its assigned question — moving state through five nodes in sequence, with checkpointing to Redis at each step.

```
POST /api/v1/tender/process
        │
        ▼
dispatch_tender_task  [queue: orchestration]
  Parse Excel → create session → build chord
        │
        ▼  (parallel — one task per question)
process_question_task × N  [queue: agents]
  LangGraph runs internally per question:
  ┌─────────────────────────────────────────┐
  │  retriever_node                         │
  │    embed question (HuggingFace)         │
  │    cosine search → pgvector             │
  │    threshold ≥ similarity_threshold     │
  │  answer_node                            │
  │    Ollama llama3.2:3b  temp=0.3         │
  │    grounded in top-3 retrieved answers  │
  │  consistency_node                       │
  │    Ollama llama3.2:3b  temp=0.1         │
  │    cross-check vs historical context    │
  │  risk_node                              │
  │    Ollama llama3.2:3b  temp=0.1         │
  │    flag overcommitments / vague claims  │
  │  formatter_node                         │
  │    compute confidence score             │
  │    build final JSON                     │
  └─────────────────────────────────────────┘
        │
        ▼  (chord callback — fires after all N complete)
aggregate_results_task  [queue: orchestration]
  Sort by question_index → update session in PostgreSQL
        │
        ▼
GET /api/v1/sessions/{session_id}
```

---

## Agents

| Agent | Role |
|---|---|
| **Retriever** | Embeds the question and runs a cosine similarity search against `chunk_embeddings`. Returns up to 5 matches above the configured threshold. |
| **Answer** | Drafts a professional response using the top 3 retrieved answers as context. Instructed not to invent certifications, SLAs, or capabilities not present in the retrieved material. |
| **Consistency** | Checks the draft against historical answers. Flags genuine contradictions — differences in phrasing are not flagged. Defaults to pass on LLM error so the pipeline never stalls. |
| **Risk** | Looks for unsupported claims, compliance overcommitments, and vague language that could be interpreted against you. Assigns low / medium / high. |
| **Formatter** | Computes a confidence score from similarity, consistency, and risk signals. Assembles the final JSON output. |

### Confidence score

```
score = 0.5 (baseline)
      + best_similarity × 0.3   (if historical match found)
      + 0.1                      (if consistency check passes)
      - 0.0 / 0.1 / 0.3         (for low / medium / high risk)
      clamped to [0.0, 1.0]

high   ≥ 0.85
medium ≥ 0.65
low    < 0.65
```

---

## Stack

| Component | Purpose |
|---|---|
| FastAPI | REST API |
| Celery + Redis | Task queue, worker pool, result backend |
| LangGraph | Per-question agent state machine with Redis checkpointing |
| PostgreSQL + pgvector | Vector store and relational metadata (single system) |
| HuggingFace sentence-transformers | Local embedding model (`all-MiniLM-L6-v2`, 384-dim) |
| Ollama (`llama3.2:3b`) | Local LLM for generation, consistency, and risk assessment |
| SQLAlchemy | ORM and session management |
| structlog | Structured JSON logging across all agents and tasks |

---

## Project layout

```
tender_master/
├── app/
│   ├── api/
│   │   ├── routes_ingest.py       # POST /historical/ingest, GET /historical/status
│   │   └── routes_tender.py       # POST /tender/process, GET /jobs/:id, GET /sessions/:id
│   ├── agents/
│   │   ├── ingestion_agent.py     # Parse + embed + store historical QA
│   │   ├── retriever_agent.py     # Cosine similarity search via pgvector
│   │   ├── answer_agent.py        # LLM response generation
│   │   ├── consistency_agent.py   # Cross-check draft against context
│   │   ├── risk_agent.py          # Compliance and claim risk assessment
│   │   └── formatter_agent.py     # Confidence scoring and output assembly
│   ├── core/
│   │   ├── config.py              # Settings (env-driven)
│   │   ├── graph.py               # LangGraph StateGraph definition
│   │   ├── state.py               # QuestionState schema
│   │   └── embeddings.py          # HuggingFace + Ollama client helpers
│   ├── db/
│   │   ├── models.py              # SQLAlchemy ORM (HistoricalChunk, ChunkEmbedding, TenderSession)
│   │   └── session.py             # Engine, session factory, init_db
│   ├── workers/
│   │   ├── celery_app.py          # Celery config, queues, worker settings
│   │   ├── tasks.py               # Orchestration tasks (dispatch, aggregate, ingest)
│   │   └── agent_tasks.py         # process_question_task (runs full LangGraph)
│   └── main.py                    # FastAPI app, lifespan, middleware
├── data/
│   └── historical/                # Sample knowledge base and tender input files
├── tests/
│   └── test_api.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Database schema

Three tables in PostgreSQL:

**`historical_chunks`** — one row per ingested Q&A pair. Stores the original question, answer, domain, source file, and the combined chunk text that was embedded.

**`chunk_embeddings`** — one row per chunk, with a 384-dimensional vector column. Kept in a separate table so the IVFFlat index stays efficient. Uses `vector_cosine_ops` with 100 lists.

**`tender_sessions`** — one row per tender submission. Tracks status (`in_progress` → `completed` / `completed_with_flags` / `completed_with_errors`), counts, and stores the full result payload as JSON.

Ingestion is idempotent. Each chunk is keyed by a SHA-256 hash of its content — uploading the same file twice inserts nothing new.

---

## Getting started

**Prerequisites:** Docker, Docker Compose, Ollama running locally.

```bash
# Pull the LLM
ollama pull llama3.2:3b

# Copy and configure environment
cp .env.example .env

# Build and start all services
docker-compose build --no-cache
docker-compose up -d

# Verify everything is healthy
docker-compose ps
```

Services:
- API: `http://localhost:8000`
- Flower (Celery monitor): `http://localhost:5555`
- RedisInsight (LangGraph checkpoints): `http://localhost:8001`

---

## Usage

### 1. Seed the knowledge base

Upload historical Q&A pairs before processing any tenders. The Excel file must have `QUESTION`, `ANSWER`, and `DOMAIN` columns. `SOURCE_FILE` is optional.

```bash
curl -X POST http://localhost:8000/api/v1/historical/ingest \
  -F "file=@data/historical/historical_qa.xlsx"
```

Check how many chunks are stored:

```bash
curl http://localhost:8000/api/v1/historical/status
```

### 2. Submit a tender

```bash
curl -X POST http://localhost:8000/api/v1/tender/process \
  -F "file=@data/new_tender_input.xlsx"
```

Response:
```json
{
  "task_id": "abc123",
  "session_id": "def456",
  "status": "accepted",
  "message": "Tender accepted. Poll /api/v1/sessions/def456 for results."
}
```

### 3. Poll for results

```bash
curl http://localhost:8000/api/v1/sessions/def456
```

Example answer in the result payload:

```json
{
  "question_index": 0,
  "original_question": "Does the platform provide native support for both SSL and TLS encryption protocols?",
  "generated_answer": "Yes, the platform provides native support for TLS 1.2 and above...",
  "domain": "Security",
  "confidence_level": "medium",
  "confidence_score": 0.68,
  "historical_alignment": {
    "has_history": true,
    "best_similarity_score": 0.9303,
    "matched_sources": [
      {
        "source_file": "tender_bank_2022.xlsx",
        "domain": "Security",
        "similarity": 0.9303
      }
    ]
  },
  "consistency": {
    "is_consistent": false,
    "notes": "Inconsistent TLS version references between draft and historical answer."
  },
  "risk_assessment": {
    "risk_level": "medium",
    "is_flagged": true,
    "notes": "Lack of specificity on legacy SSL support could be misinterpreted."
  },
  "status": "completed",
  "error": null
}
```

---

## Celery queues

| Queue | Tasks |
|---|---|
| `ingestion` | `ingest_historical_task` |
| `orchestration` | `dispatch_tender_task`, `aggregate_results_task` |
| `agents` | `process_question_task` (full LangGraph run per question) |

Scale agent workers independently from orchestration workers:

```bash
celery -A app.workers.celery_app worker --queues=agents --concurrency=4
```

Worker settings worth knowing:
- `task_acks_late=True` — tasks are re-queued if a worker crashes mid-execution
- `worker_prefetch_multiplier=1` — one task per worker at a time, appropriate for LLM inference
- `time_limit=300` on agent tasks — prevents hung LLM calls from blocking workers indefinitely

---

## Monitoring

**Flower** at `http://localhost:5555` — task success rates, worker health, queue depths.

**RedisInsight** at `http://localhost:8001` — inspect LangGraph JSON checkpoints stored per question thread.

---

## Known limitations

- **Cross-question consistency within a session is not enforced.** Because all questions run in parallel, there is no safe way to share a session answers list without race conditions. The consistency agent checks against historical data only. A post-processing pass in the aggregator would be the right place to address this.
- **No metadata filtering on retrieval.** The vector search queries all domains equally. A pre-filter by domain (classify the question first, then add `WHERE domain = :domain`) would improve precision.
- **llama3.2:3b is a small model.** It works for structured JSON outputs at low temperature, but a larger model improves answer quality significantly. The LLM is swappable via the `LLM_MODEL` environment variable.
- **No authentication.** The API is open. Add a middleware layer before any production deployment.
