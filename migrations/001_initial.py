"""Initial schema — historical chunks + embeddings + tender sessions

Revision ID: 001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


def upgrade():
    # Enable pgvector
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "historical_chunks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_file", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(100), nullable=False),
        sa.Column("original_question", sa.Text, nullable=False),
        sa.Column("original_answer", sa.Text, nullable=False),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_historical_chunks_domain", "historical_chunks", ["domain"])

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("chunk_id", sa.String(64), sa.ForeignKey("historical_chunks.id", ondelete="CASCADE"), unique=True),
        sa.Column("embedding", sa.Text, nullable=False),  # actual Vector type managed by SQLAlchemy model
    )

    op.create_table(
        "tender_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("total_questions", sa.Integer, default=0),
        sa.Column("completed_count", sa.Integer, default=0),
        sa.Column("flagged_count", sa.Integer, default=0),
        sa.Column("overall_status", sa.String(50), default="in_progress"),
        sa.Column("result_payload", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_tender_sessions_task_id", "tender_sessions", ["task_id"])


def downgrade():
    op.drop_table("tender_sessions")
    op.drop_table("chunk_embeddings")
    op.drop_table("historical_chunks")
