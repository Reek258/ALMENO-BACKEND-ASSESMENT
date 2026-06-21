import json
import re
import time
import logging

from google import genai

from app.config import settings

logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = [
    "Food", "Shopping", "Travel", "Transport",
    "Utilities", "Cash Withdrawal", "Entertainment", "Other",
]

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _extract_json(text: str) -> dict:
    """Strip markdown code-fences etc. and parse the first JSON object found."""
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(match.group(0))


def _call_with_retry(prompt: str, max_retries: int = 3) -> str:
    """Calls Gemini, retrying up to max_retries times with exponential backoff.

    Raises the last exception if every attempt fails -- callers are expected
    to catch this and mark the relevant batch as llm_failed rather than
    failing the whole job (per spec, step e).
    """
    client = _get_client()

    last_exc = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
            )
            return response.text
        except Exception as exc:  # noqa: BLE001 -- genuinely want to catch any SDK/network error here
            last_exc = exc
            wait = 2 ** attempt
            logger.warning("Gemini call failed (attempt %s/%s): %s. Retrying in %ss", attempt + 1, max_retries, exc, wait)
            time.sleep(wait)
    raise last_exc


def classify_batch(rows: list[dict]) -> dict[str, str]:
    """Classify a batch of transactions missing a category.

    `rows` is a list of dicts with txn_id, merchant, amount, currency, notes.
    Returns {txn_id: category}. Raises on total failure so the caller can
    mark the batch llm_failed.
    """
    if not rows:
        return {}

    lines = "\n".join(
        f'- txn_id="{r["txn_id"]}", merchant="{r["merchant"]}", amount={r["amount"]}, '
        f'currency="{r["currency"]}", notes="{r["notes"]}"'
        for r in rows
    )
    prompt = f"""You are classifying financial transactions into spending categories.
Allowed categories (use exactly one, verbatim): {", ".join(ALLOWED_CATEGORIES)}

Transactions:
{lines}

Respond with ONLY a JSON object mapping each txn_id to its category, nothing else.
Example: {{"TXN1001": "Food", "TXN1002": "Travel"}}"""

    raw = _call_with_retry(prompt)
    parsed = _extract_json(raw)
    # Defend against the model inventing a category outside the allowed set.
    return {k: (v if v in ALLOWED_CATEGORIES else "Other") for k, v in parsed.items()}


def generate_narrative(stats: dict) -> dict:
    """Single LLM call producing {narrative, risk_level} from precomputed stats.

    Totals/top-merchants/anomaly-count are computed deterministically in
    Python (see tasks.py) rather than asked of the LLM -- arithmetic over the
    full transaction set is exactly the kind of thing an LLM can silently get
    wrong, while a sum() cannot. The LLM's value-add here is turning those
    numbers into a narrative and a qualitative risk call, which IS a judgment
    task suited to it.
    """
    prompt = f"""Given this spending summary for a personal/business account:
{json.dumps(stats, indent=2)}

Respond with ONLY a JSON object of the form:
{{"narrative": "2-3 sentence plain-English summary of the spending pattern", "risk_level": "low|medium|high"}}
risk_level should reflect the anomaly_count and proportion of failed transactions relative to total transactions."""

    raw = _call_with_retry(prompt)
    parsed = _extract_json(raw)
    if parsed.get("risk_level") not in ("low", "medium", "high"):
        parsed["risk_level"] = "medium"
    return parsed
