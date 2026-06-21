import enum
import uuid

from sqlalchemy import (
    Column, String, Integer, Numeric, Boolean, DateTime, ForeignKey, Text, Enum, JSON, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class JobStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String, nullable=False)
    status = Column(Enum(JobStatus), nullable=False, default=JobStatus.pending, index=True)
    row_count_raw = Column(Integer, nullable=True)
    row_count_clean = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    transactions = relationship("Transaction", back_populates="job", cascade="all, delete-orphan")
    summary = relationship("JobSummary", back_populates="job", uselist=False, cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False, index=True)

    txn_id = Column(String, nullable=False)
    date = Column(String, nullable=True)  # stored as ISO-8601 string after cleaning
    merchant = Column(String, nullable=True)
    amount = Column(Numeric(14, 2), nullable=True)
    currency = Column(String, nullable=True)
    status = Column(String, nullable=True)
    category = Column(String, nullable=True)
    account_id = Column(String, nullable=True)
    notes = Column(String, nullable=True)

    is_anomaly = Column(Boolean, default=False)
    anomaly_reason = Column(String, nullable=True)

    llm_category = Column(String, nullable=True)
    llm_raw_response = Column(Text, nullable=True)
    llm_failed = Column(Boolean, default=False)

    job = relationship("Job", back_populates="transactions")


class JobSummary(Base):
    __tablename__ = "job_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False, unique=True)

    total_spend_inr = Column(Numeric(14, 2), default=0)
    total_spend_usd = Column(Numeric(14, 2), default=0)
    top_merchants = Column(JSON, nullable=True)
    anomaly_count = Column(Integer, default=0)
    narrative = Column(Text, nullable=True)
    risk_level = Column(String, nullable=True)
    llm_narrative_failed = Column(Boolean, default=False)

    job = relationship("Job", back_populates="summary")
