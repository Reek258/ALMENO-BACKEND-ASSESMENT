import pandas as pd

# Brands that only operate domestically -- a USD-denominated transaction
# against one of these is almost certainly a data error or a flag-worthy anomaly.
DOMESTIC_ONLY_BRANDS = {"swiggy", "ola", "irctc"}


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Adds `is_anomaly` (bool) and `anomaly_reason` (str) columns in place.

    Two required checks, applied independently (a row can trigger both,
    reasons are joined with '; '):
      1. amount > 3x the median amount for that account_id
      2. currency == USD but merchant is a known domestic-only brand
    A third, lighter-weight check is included as a bonus: notes containing
    'SUSPICIOUS' (case-insensitive) are surfaced too, since the source data
    clearly intends that as an analyst-provided signal worth not throwing away.
    """
    df = df.copy()
    df["is_anomaly"] = False
    df["anomaly_reason"] = ""

    median_by_account = df.groupby("account_id")["amount"].median()

    def reasons_for_row(row):
        reasons = []
        median = median_by_account.get(row["account_id"], None)
        if median is not None and median > 0 and row["amount"] > 3 * median:
            reasons.append("statistical_outlier_3x_account_median")
        if row["currency"] == "USD" and row["merchant"].strip().lower() in DOMESTIC_ONLY_BRANDS:
            reasons.append("usd_currency_on_domestic_only_merchant")
        if "suspicious" in row["notes"].lower():
            reasons.append("notes_flagged_suspicious")
        return reasons

    reason_lists = df.apply(reasons_for_row, axis=1)
    df["is_anomaly"] = reason_lists.apply(lambda r: len(r) > 0)
    df["anomaly_reason"] = reason_lists.apply(lambda r: "; ".join(r) if r else None)

    return df
