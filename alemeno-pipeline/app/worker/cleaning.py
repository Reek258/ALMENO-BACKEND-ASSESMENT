import re
import uuid
from datetime import datetime

import pandas as pd

VALID_CURRENCIES = {"INR", "USD"}
VALID_STATUSES = {"SUCCESS", "FAILED", "PENDING"}

# The brief documents two date formats (DD-MM-YYYY and YYYY/MM/DD), but the
# real file also contains a few rows already in ISO (YYYY-MM-DD) -- both use
# '-' as a separator, so we can't tell them apart by separator alone and need
# explicit shape-matching instead.
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_DMY_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")


def _normalise_date(raw: str) -> str:
    """Convert any of the three observed date shapes into ISO-8601."""
    raw = str(raw).strip()
    if _ISO_RE.match(raw):
        dt = datetime.strptime(raw, "%Y-%m-%d")
    elif _SLASH_RE.match(raw):
        dt = datetime.strptime(raw, "%Y/%m/%d")
    elif _DMY_RE.match(raw):
        dt = datetime.strptime(raw, "%d-%m-%Y")
    else:
        raise ValueError(f"Unrecognised date format: {raw!r}")
    return dt.date().isoformat()


def _normalise_amount(raw) -> float:
    s = str(raw).strip().replace("$", "").replace(",", "")
    return float(s)


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all step-(a) cleaning rules and return a clean dataframe.

    Adds a transient `_category_missing` column flagging rows whose category
    was blank in the source file -- the LLM classification step (c) needs to
    know this *before* we overwrite blanks with the 'Uncategorised' placeholder.
    """
    df = df.copy()

    df["_category_missing"] = df["category"].isna() | (df["category"].astype(str).str.strip() == "")

    df["date"] = df["date"].apply(_normalise_date)
    df["amount"] = df["amount"].apply(_normalise_amount)
    df["currency"] = df["currency"].astype(str).str.strip().str.upper()
    df["status"] = df["status"].astype(str).str.strip().str.upper()
    df["category"] = df["category"].apply(
        lambda c: "Uncategorised" if pd.isna(c) or str(c).strip() == "" else str(c).strip()
    )
    df["merchant"] = df["merchant"].astype(str).str.strip()
    df["account_id"] = df["account_id"].astype(str).str.strip()
    df["notes"] = df["notes"].fillna("").astype(str).str.strip()

    # Exact-duplicate removal happens AFTER normalisation: two rows that only
    # differed by casing (e.g. "success" vs "SUCCESS") are the same transaction
    # once cleaned, and should collapse into one row.
    df = df.drop_duplicates(
        subset=[c for c in df.columns if c != "_category_missing"], keep="first"
    ).reset_index(drop=True)

    # Fill missing txn_id AFTER dedup, so two genuinely blank-id rows that are
    # otherwise identical are still treated as duplicates rather than both
    # getting distinct random ids and surviving as "different" transactions.
    df["txn_id"] = df["txn_id"].apply(
        lambda t: f"GEN-{uuid.uuid4().hex[:8]}" if pd.isna(t) or str(t).strip() == "" else str(t).strip()
    )

    return df
