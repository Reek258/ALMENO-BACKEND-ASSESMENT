from typing import Optional
from uuid import UUID

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Job, Transaction, JobStatus
from app.schemas import JobUploadResponse, JobStatusResponse, JobListItem, JobResultsResponse, TransactionOut
from app.storage import save_upload
from app.queue import task_queue
from app.worker.tasks import process_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/upload", response_model=JobUploadResponse)
def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")

    file_bytes = file.file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    saved_path = save_upload(file_bytes, file.filename)

    job = Job(filename=file.filename, status=JobStatus.pending)
    db.add(job)
    db.commit()
    db.refresh(job)

    # Enqueue by job id + path only -- never pass DB sessions or large payloads
    # into the queue, RQ serializes arguments and a session object isn't picklable.
    task_queue.enqueue(process_job, str(job.id), saved_path, job_timeout=600)

    return JobUploadResponse(job_id=job.id, status=job.status.value)


@router.get("/{job_id}/status", response_model=JobStatusResponse)
def get_job_status(job_id: UUID, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    summary = None
    if job.status == JobStatus.completed and job.summary:
        summary = {
            "total_spend_inr": float(job.summary.total_spend_inr or 0),
            "total_spend_usd": float(job.summary.total_spend_usd or 0),
            "top_merchants": job.summary.top_merchants,
            "anomaly_count": job.summary.anomaly_count,
            "risk_level": job.summary.risk_level,
        }

    return JobStatusResponse(
        job_id=job.id,
        filename=job.filename,
        status=job.status.value,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        created_at=job.created_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        summary=summary,
    )


@router.get("/{job_id}/results", response_model=JobResultsResponse)
def get_job_results(job_id: UUID, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.completed:
        raise HTTPException(status_code=409, detail=f"Job is '{job.status.value}', not completed yet")

    txns = db.query(Transaction).filter(Transaction.job_id == job_id).all()
    txn_out = [TransactionOut.model_validate(t) for t in txns]
    anomalies = [t for t in txn_out if t.is_anomaly]

    category_breakdown: dict = {}
    for t in txns:
        cat = t.category or "Uncategorised"
        category_breakdown[cat] = category_breakdown.get(cat, 0) + float(t.amount or 0)

    summary = None
    if job.summary:
        summary = {
            "total_spend_inr": float(job.summary.total_spend_inr or 0),
            "total_spend_usd": float(job.summary.total_spend_usd or 0),
            "top_merchants": job.summary.top_merchants,
            "anomaly_count": job.summary.anomaly_count,
            "narrative": job.summary.narrative,
            "risk_level": job.summary.risk_level,
            "llm_narrative_failed": job.summary.llm_narrative_failed,
        }

    return JobResultsResponse(
        job_id=job.id,
        status=job.status.value,
        transactions=txn_out,
        anomalies=anomalies,
        category_breakdown=category_breakdown,
        summary=summary,
    )


@router.get("", response_model=list[JobListItem])
def list_jobs(status: Optional[str] = Query(None), db: Session = Depends(get_db)):
    query = db.query(Job)
    if status:
        try:
            status_enum = JobStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status filter: {status}")
        query = query.filter(Job.status == status_enum)

    jobs = query.order_by(Job.created_at.desc()).all()
    return [
        JobListItem(
            job_id=j.id,
            filename=j.filename,
            status=j.status.value,
            row_count_raw=j.row_count_raw,
            created_at=j.created_at,
        )
        for j in jobs
    ]
