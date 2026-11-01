"""
Deterministic referral triage.

Ingests the partner referral/consent extract, normalizes contact data, keeps
only prospects the English-language voice agent can serve, and assigns a
priority score so the Voice AI dials the right person first.

Everything in this module is rule-based on purpose: consent, language and
contact completeness are compliance decisions and should be auditable, not
inferred by a model.
"""

from __future__ import annotations

import re

import pandas as pd

# The voice agent only handles English today; other languages are routed to a
# human Outreach Specialist with Propio live translation.
AGENT_LANGUAGES = {"ENGLISH", "", "NAN"}

# Placeholder numbers that show up in partner extracts when a field is unknown.
INVALID_PHONES = {"0000000000"}


def normalize_phone(raw: object) -> str | None:
    """Return a 10-digit US number or None.

    Handles the formats seen in referral extracts: ``(916) 472-5389 (H)``,
    ``916-839-1431(M)``, ``+1 916 472 5389``, bare digits, NaN.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits in INVALID_PHONES:
        return None
    return digits


def format_phone_e164(ten_digits: str) -> str:
    return f"+1{ten_digits}"


def _has_explicit_consent(response: object) -> bool:
    text = str(response or "").lower()
    return "explicit consent" in text and text.strip().startswith("yes")


def score_row(row: pd.Series) -> pd.Series:
    """Priority scoring rules. Higher score dials first."""
    score = 10  # baseline for any referral in queue
    reasons: list[str] = []

    if _has_explicit_consent(row.get("DOTPHRASE_RESPONSE")):
        score += 30
        reasons.append("Explicit consent verified")
    else:
        reasons.append("NO CONSENT ON FILE - do not dial")

    alt_phones = [
        normalize_phone(row.get(col))
        for col in ("ALT_CONTACT_PHONE", "ALT_CONTACT1_PHONE", "ALT_CONTACT2_PHONE")
    ]
    if any(alt_phones):
        score += 15
        reasons.append("Alternate contact available")

    if not row.get("CLEAN_PHONE"):
        score -= 25
        reasons.append("Primary phone missing/invalid")

    if pd.isna(row.get("DOB")) or not str(row.get("DOB")).strip():
        score -= 10
        reasons.append("DOB missing - PHI verification will fail")

    return pd.Series([score, ", ".join(reasons)], index=["PRIORITY_SCORE", "ROUTING_REASONS"])


def load_referrals(referrals_path: str) -> pd.DataFrame:
    df = pd.read_csv(referrals_path, dtype=str)
    df["FULL_NAME"] = (
        df["FIRST_NAME"].fillna("").str.strip() + " " + df["LAST_NAME"].fillna("").str.strip()
    ).str.strip()
    df["CLEAN_PHONE"] = df["CONTACT_PHONE"].map(normalize_phone)
    df["LANGUAGE_NORM"] = df["PREFERRED_LANGUAGE"].fillna("").str.strip().str.upper()
    return df


def load_and_triage_referrals(referrals_path: str) -> pd.DataFrame:
    """Full pipeline: load -> language filter -> score -> sort.

    Returns only prospects the English voice agent should dial, highest
    priority first. Sort is stable so ties keep referral-file order
    (oldest referral in the batch dials first).
    """
    df = load_referrals(referrals_path)
    df_english = df[df["LANGUAGE_NORM"].isin(AGENT_LANGUAGES)].copy()

    scores = df_english.apply(score_row, axis=1)
    df_english = pd.concat([df_english, scores], axis=1)
    df_english["CALLABLE"] = df_english["CLEAN_PHONE"].notna() & df_english[
        "DOTPHRASE_RESPONSE"
    ].map(_has_explicit_consent)

    return df_english.sort_values(
        by=["CALLABLE", "PRIORITY_SCORE"], ascending=[False, False], kind="mergesort"
    ).reset_index(drop=True)


def non_english_handoffs(referrals_path: str) -> pd.DataFrame:
    """Prospects that need a human OS + Propio interpreter instead of the agent."""
    df = load_referrals(referrals_path)
    return df[~df["LANGUAGE_NORM"].isin(AGENT_LANGUAGES)][
        ["FULL_NAME", "PREFERRED_LANGUAGE", "CLEAN_PHONE", "DEPARTMENT_NAME"]
    ].reset_index(drop=True)
