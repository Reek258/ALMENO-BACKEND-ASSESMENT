from datetime import datetime
from typing import Optional, List, Any
from uuid import UUID

from pydantic import BaseModel


class JobUploadResponse(BaseModel):
    job_id: UUID
    status: str


class JobStatusResponse(BaseModel):
    job_id: UUID
    filename: str
    status: str
    row_count_raw: Optional[int]
    row_count_clean: Optional[int]
    created_at: datetime
    completed_at: Optional[datetime]
    error_message: Optional[str]
    summary: Optional[dict] = None


class JobListItem(BaseModel):
    job_id: UUID
    filename: str
    status: str
    row_count_raw: Optional[int]
    created_at: datetime


class TransactionOut(BaseModel):
    txn_id: str
    date: Optional[str]
    merchant: Optional[str]
    amount: Optional[float]
    currency: Optional[str]
    status: Optional[str]
    category: Optional[str]
    account_id: Optional[str]
    notes: Optional[str]
    is_anomaly: bool
    anomaly_reason: Optional[str]
    llm_category: Optional[str]
    llm_failed: bool

    class Config:
        from_attributes = True


class JobResultsResponse(BaseModel):
    job_id: UUID
    status: str
    transactions: List[TransactionOut]
    anomalies: List[TransactionOut]
    category_breakdown: dict
    summary: Optional[dict]
