"""
Shared state schema for the LangGraph tender processing pipeline.

Short-Term State  : per-question processing state (QuestionState)
Long-Term Memory  : session-level aggregation (TenderSessionState)
"""
from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field
from enum import Enum


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    RETRIEVING = "retrieving"
    ANSWERING = "answering"
    CHECKING_CONSISTENCY = "checking_consistency"
    ASSESSING_RISK = "assessing_risk"
    FORMATTING = "formatting"
    COMPLETED = "completed"
    FAILED = "failed"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class HistoricalMatch(BaseModel):
    chunk_id: str
    question: str
    answer: str
    domain: str
    similarity_score: float
    source_file: str


class QuestionState(BaseModel):
    """Short-term state: lives for the duration of one question's pipeline run."""
    question_index: int
    original_question: str
    domain: str = ""

    # Retriever outputs
    historical_matches: list[HistoricalMatch] = Field(default_factory=list)
    has_history: bool = False
    best_similarity: float = 0.0

    # Answer agent outputs
    draft_answer: str = ""

    # Consistency agent outputs
    is_consistent: bool = True
    consistency_notes: str = ""

    # Risk agent outputs
    risk_level: RiskLevel = RiskLevel.LOW
    risk_notes: str = ""
    is_flagged: bool = False

    # Final formatted output
    formatted_answer: dict[str, Any] = Field(default_factory=dict)

    # Processing metadata
    status: ProcessingStatus = ProcessingStatus.PENDING
    error: Optional[str] = None


class TenderSessionState(BaseModel):
    """Long-term memory: persists across all questions in one tender session."""
    session_id: str
    task_id: str
    source_filename: str

    # All questions to process
    questions: list[str] = Field(default_factory=list)

    # Results keyed by question index
    completed_questions: dict[int, QuestionState] = Field(default_factory=dict)
    failed_questions: list[int] = Field(default_factory=list)

    # Aggregates
    total_questions: int = 0
    processed_count: int = 0
    flagged_count: int = 0

    # Used by consistency agent to cross-check answers within the session
    session_answers_so_far: list[dict[str, str]] = Field(default_factory=list)

    # Overall status
    overall_status: str = "in_progress"

    def to_summary(self) -> dict[str, Any]:
        answers = [
            q.formatted_answer
            for q in self.completed_questions.values()
        ]
        return {
            "session_id": self.session_id,
            "source_filename": self.source_filename,
            "total_questions": self.total_questions,
            "completed": len(self.completed_questions),
            "failed": len(self.failed_questions),
            "flagged_count": self.flagged_count,
            "overall_status": self.overall_status,
            "answers": answers,
        }


# LangGraph requires plain dicts as state — these helpers convert
def question_state_to_dict(qs: QuestionState) -> dict:
    return qs.model_dump()


def dict_to_question_state(d: dict) -> QuestionState:
    return QuestionState(**d)
