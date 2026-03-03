# Pan Software — Multi-Agent Tender Response Automation Service

An intelligent, asynchronous pipeline for generating high-quality tender responses. This system leverages **LangGraph** for sophisticated agentic workflows and **Celery** for horizontal scaling.

## 🏗️ Architecture

The system splits orchestration into two layers to balance internal logic complexity with external processing scale:

* **Macro-Orchestration (Celery):** Handles file parsing and fans out individual questions as parallel tasks across the worker cluster.
* **Micro-Orchestration (LangGraph):** Each worker runs a self-contained State Machine for a single question, managing state transitions between agents and persistence via Redis Stack.


```
FastAPI (REST)  ──►  Celery Workers  ──►  LangGraph Orchestrator
                                               │
                    ┌──────────────────────────┼──────────────────────────┐
                    ▼                          ▼                          ▼
             IngestionAgent          RetrieverAgent              AnswerAgent
             (RAG pre-process)       (Semantic search)           (LLM draft)
                    │                          │                          │
             PostgreSQL +               pgvector similarity        ConsistencyAgent
             pgvector                   search (≥0.75)                    │
                                                                    RiskAgent
                                                                          │
                                                                   FormatterAgent
                                                                          │
                                                                     JSON Output
```

## Stack
- **FastAPI** — REST API (Celery worker)
- **Celery + Redis** — Task queue / worker pool
- **LangGraph** — Agent orchestration with checkpointing
- **PostgreSQL + pgvector** — Vector store + metadata RDBMS
- **Ollama (llama3.2:3b)** — LLM for answer generation
- **SQLAlchemy** — ORM

## Project Layout
```
tender_system/
├── app/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes_ingest.py      
│   │   └── routes_tender.py     
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── ingestion_agent.py
│   │   ├── retriever_agent.py
│   │   ├── answer_agent.py
│   │   ├── consistency_agent.py
│   │   ├── risk_agent.py
│   │   └── formatter_agent.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── graph.py              
│   │   └── state.py              
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   └── session.py
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── celery_app.py
│   │   └── tasks.py
│   └── main.py
├── data/
│   └── historical/              
├── tests/
│   └── test_api.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```


---

## 🤖 Agent Definitions

| Agent | Responsibility | Logic |
| :--- | :--- | :--- |
| **Ingestion** | Knowledge Base Setup | Processes historical QA into `pgvector` embeddings. |
| **Retriever** | Semantic Search | Finds the top 5 historical matches with a similarity threshold of $\ge 0.75$. |
| **Answer** | Content Generation | Drafts a professional response grounded strictly in retrieved context. |
| **Consistency** | Cross-Check | Verifies the answer against previous session answers and history. |
| **Risk** | Safety Review | Flags unsupported claims or legal/compliance overcommitments. |
| **Formatter** | Structured Output | Computes a confidence score and builds the final JSON payload. |

---

## 🚀 Deployment & Verification

### 1. Prerequisites
* Docker & Docker Compose.
* Ollama (running locally or via the included container).

### 2. Commands to Deploy
Run the following commands to build the containers without cache and start the services in detached mode:

```bash
docker-compose build --no-cache
docker-compose up -d


```

### 3. Verify

Check that all services and modules (specifically **RedisJSON**) are healthy:

```bash
# Check container status
docker-compose ps

```

---

## 📂 Data & Examples

Reference the `/data` folder for templates and sample outputs:

* **Sample Historical Data:** `data/historical/sample_knowledge_base.xlsx` — Use this to seed the RAG system.
* **Sample Input Excel:** `data/tenders/new_tender_input.xlsx` — A template for new questionnaires.
* **Example Output:** `data/samples/output_response.json` — Detailed breakdown of the generated answers, risk levels, and confidence scores.

---

## 📡 Postman Calls

Import `postman_calls_collection.json` into Postman for ready-to-use requests.

### 1. Process New Tender

**POST** `/api/v1/tender/process`

* **Input:** Multi-part form-data (Excel file).
* **Sample Response Payload:**
```json
{
  "task_id": "uuid-celery-task",
  "session_id": "uuid-db-session",
  "status": "accepted",
  "message": "Tender accepted. Poll /api/v1/sessions/{session_id} for results."
}

```



### 2. Poll Session Results

**GET** `/api/v1/sessions/{session_id}`

* **Sample Response Payload (Completed):**
```json
{
                "question_index": 0,
                "original_question": "Does the platform provide native support for both SSL and TLS encryption protocols?",
                "generated_answer": "Yes, the platform provides native support for both SSL and TLS encryption protocols, with a focus on TLS 1.2 and higher versions, as well as legacy SSL support.",
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
                    "notes": "Inconsistent version of TLS supported (TLS 1.2+ vs TLS 1.2+ with 'higher versions') and mention of legacy SSL without specifying version"
                },
                "risk_assessment": {
                    "risk_level": "medium",
                    "is_flagged": true,
                    "notes": "Inconsistent versioning and lack of specificity on legacy SSL support could lead to confusion or misinterpretation."
                },
                "status": "completed",
                "error": null
            }

```



---

## 📈 Monitoring

* **Flower UI:** Monitor Celery worker health and task success rates at `http://localhost:5555`.
* **RedisInsight:** View LangGraph JSON checkpoints and thread states at `http://localhost:8001`.

