"""
SQLAlchemy ORM models.

Tables:
  - historical_chunks     : metadata for each ingested Q&A chunk
  - historical_embeddings : pgvector embeddings (1536-dim)
  - tender_sessions       : audit log of each tender processing run
"""
import os
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Float, Integer,
    DateTime, Boolean, JSON, ForeignKey, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship
from pgvector.sqlalchemy import Vector


# Read from env so it matches the chosen sentence-transformer model
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))  # default: all-MiniLM-L6-v2


class Base(DeclarativeBase):
    pass


class HistoricalChunk(Base):
    __tablename__ = "historical_chunks"

    id = Column(String(64), primary_key=True)           # uuid4 hex
    source_file = Column(String(255), nullable=False)
    domain = Column(String(100), nullable=False, index=True)
    original_question = Column(Text, nullable=False)
    original_answer = Column(Text, nullable=False)
    chunk_text = Column(Text, nullable=False)            # combined Q+A text fed to embedder
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship to vector
    embedding = relationship("ChunkEmbedding", back_populates="chunk", uselist=False, cascade="all, delete-orphan")


class ChunkEmbedding(Base):
    """Separate table keeps pgvector index efficient."""
    __tablename__ = "chunk_embeddings"

    id = Column(String(64), primary_key=True)           # same as chunk id
    chunk_id = Column(String(64), ForeignKey("historical_chunks.id", ondelete="CASCADE"), unique=True)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)

    chunk = relationship("HistoricalChunk", back_populates="embedding")

    __table_args__ = (
        Index(
            "ix_chunk_embeddings_vector",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class TenderSession(Base):
    __tablename__ = "tender_sessions"

    id = Column(String(64), primary_key=True)
    task_id = Column(String(64), nullable=False, index=True)
    source_filename = Column(String(255), nullable=False)
    total_questions = Column(Integer, default=0)
    completed_count = Column(Integer, default=0)
    flagged_count = Column(Integer, default=0)
    overall_status = Column(String(50), default="in_progress")
    result_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
