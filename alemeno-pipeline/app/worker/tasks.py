import logging
import uuid
from datetime import datetime, timezone

import pandas as pd

from app.database import SessionLocal
from app.models import Job, Transaction, JobSummary, JobStatus
from app.worker.cleaning import clean_dataframe
from app.worker.anomaly import detect_anomalies
from app.worker import llm

logger = logging.getLogger(__name__)

CLASSIFICATION_BATCH_SIZE = 15
EXPECTED_COLUMNS = ["txn_id", "date", "merchant", "amount", "currency", "status", "category", "account_id", "notes"]


def _chunks(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def process_job(job_id: str, csv_path: str) -> None:
    """Entry point invoked by the RQ worker for each uploaded CSV.

    Runs synchronously inside the worker process. Any unexpected exception
    anywhere in this function is caught at the bottom and recorded on the Job
    row as status=failed + error_message, so a single bad file never crashes
    the worker process or leaves a job stuck in 'processing' forever.
    """
    db = SessionLocal()
    job_uuid = uuid.UUID(job_id)
    try:
        job = db.query(Job).filter(Job.id == job_uuid).first()
        if not job:
            logger.error("process_job: job %s not found", job_id)
            return

        job.status = JobStatus.processing
        db.commit()

        df_raw = pd.read_csv(csv_path)
        for col in EXPECTED_COLUMNS:
            if col not in df_raw.columns:
                df_raw[col] = None
        job.row_count_raw = len(df_raw)

        # --- (a) Data cleaning ---
        df = clean_dataframe(df_raw)
        job.row_count_clean = len(df)

        # --- (b) Anomaly detection ---
        df = detect_anomalies(df)

        # --- (c) LLM classification, batched ---
        needs_classification = df[df["_category_missing"]].to_dict("records")
        category_map: dict[str, str] = {}
        any_batch_failed = False

        for batch in _chunks(needs_classification, CLASSIFICATION_BATCH_SIZE):
            try:
                result = llm.classify_batch(
                    [
                        {
                            "txn_id": r["txn_id"],
                            "merchant": r["merchant"],
                            "amount": r["amount"],
                            "currency": r["currency"],
                            "notes": r["notes"],
                        }
                        for r in batch
                    ]
                )
                category_map.update(result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Classification batch failed after retries: %s", exc)
                any_batch_failed = True

        # --- Persist transactions ---
        for _, row in df.iterrows():
            llm_cat = category_map.get(row["txn_id"])
            final_category = llm_cat if llm_cat else row["category"]
            txn = Transaction(
                job_id=job.id,
                txn_id=row["txn_id"],
                date=row["date"],
                merchant=row["merchant"],
                amount=float(row["amount"]),
                currency=row["currency"],
                status=row["status"],
                category=final_category,
                account_id=row["account_id"],
                notes=row["notes"],
                is_anomaly=bool(row["is_anomaly"]),
                anomaly_reason=row["anomaly_reason"],
                llm_category=llm_cat,
                llm_failed=bool(row["_category_missing"] and not llm_cat),
            )
            db.add(txn)
        db.commit()

        # --- (d) LLM narrative summary ---
        total_inr = float(df[df["currency"] == "INR"]["amount"].sum())
        total_usd = float(df[df["currency"] == "USD"]["amount"].sum())
        top_merchants = (
            df.groupby("merchant")["amount"].sum().sort_values(ascending=False).head(3)
        )
        top_merchants_list = [{"merchant": m, "total_amount": float(v)} for m, v in top_merchants.items()]
        anomaly_count = int(df["is_anomaly"].sum())
        failed_ratio = float((df["status"] == "FAILED").mean())

        stats_for_llm = {
            "total_spend_inr": total_inr,
            "total_spend_usd": total_usd,
            "top_merchants": top_merchants_list,
            "anomaly_count": anomaly_count,
            "total_transactions": len(df),
            "failed_transaction_ratio": round(failed_ratio, 3),
        }

        narrative_failed = False
        narrative_text = None
        risk_level = None
        try:
            narrative_result = llm.generate_narrative(stats_for_llm)
            narrative_text = narrative_result.get("narrative")
            risk_level = narrative_result.get("risk_level")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Narrative generation failed after retries: %s", exc)
            narrative_failed = True
            risk_level = "medium" if anomaly_count > 0 else "low"

        summary = JobSummary(
            job_id=job.id,
            total_spend_inr=total_inr,
            total_spend_usd=total_usd,
            top_merchants=top_merchants_list,
            anomaly_count=anomaly_count,
            narrative=narrative_text,
            risk_level=risk_level,
            llm_narrative_failed=narrative_failed,
        )
        db.add(summary)

        job.status = JobStatus.completed
        job.completed_at = datetime.now(timezone.utc)
        if any_batch_failed:
            job.error_message = "One or more LLM classification batches failed after retries; affected rows kept category 'Uncategorised' (see llm_failed flag per transaction)."
        db.commit()

    except Exception as exc:  # noqa: BLE001
        logger.exception("process_job failed for job %s", job_id)
        db.rollback()
        job = db.query(Job).filter(Job.id == job_uuid).first()
        if job:
            job.status = JobStatus.failed
            job.error_message = str(exc)
            db.commit()
    finally:
        db.close()
