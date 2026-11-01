"""
Voice AI outreach agent.

Two modes share one call script (the system prompt):

* ``run_local_simulation``  - deterministic, offline walkthrough of the call
  (Demo Mode). No API key, no network.
* ``trigger_live_vapi_call`` - places a real outbound call through Vapi's REST
  API (Live Mode). Requires VAPI_API_KEY and VAPI_PHONE_NUMBER_ID.

Deterministic code decides *who* gets called and *what must be verified*;
the LLM only handles the conversation itself.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import requests

from .triage import format_phone_e164, normalize_phone

VAPI_BASE_URL = os.getenv("VAPI_BASE_URL", "https://api.vapi.ai")
REQUEST_TIMEOUT_S = 20

# Structured fields Vapi extracts from the transcript after the call ends.
# This is what gets written back to the CRM as the Welcome Call outcome.
CALL_OUTCOME_SCHEMA = {
    "type": "object",
    "properties": {
        "identity_verified": {
            "type": "boolean",
            "description": "True only if name, DOB and street address all matched.",
        },
        "lock_in_acknowledged": {
            "type": "boolean",
            "description": "Prospect confirmed they understand Habitat Health becomes their sole provider.",
        },
        "interested": {
            "type": "boolean",
            "description": "Prospect is open to receiving services under PACE.",
        },
        "next_step_preference": {
            "type": "string",
            "enum": ["NURSE_HOME_VISIT", "CENTER_TOUR", "CALLBACK_LATER", "DECLINED", "UNKNOWN"],
        },
        "callback_notes": {
            "type": "string",
            "description": "Anything an Enrollment Specialist needs to know before following up.",
        },
    },
    "required": ["identity_verified", "lock_in_acknowledged", "interested", "next_step_preference"],
}


def _spoken_dob(dob: object) -> str:
    """'07/02/1948' -> 'July 2, 1948' so the model verifies what people actually say."""
    text = str(dob or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"
        except ValueError:
            continue
    return text or "UNKNOWN"


class VoiceAIOutreachAgent:
    def __init__(self, prospect_data: dict[str, Any]):
        self.prospect = prospect_data
        self.api_key = os.getenv("VAPI_API_KEY")
        self.phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID")

    # ------------------------------------------------------------------ script
    @property
    def name(self) -> str:
        return str(self.prospect.get("FULL_NAME", "")).strip()

    @property
    def first_message(self) -> str:
        return (
            f"Hello, this is the Habitat Health PACE program calling. "
            f"Am I speaking with {self.name}?"
        )

    def build_system_prompt(self) -> str:
        dept = self.prospect.get("DEPARTMENT_NAME") or "your clinic"
        clinician = self.prospect.get("REFERRING_CLINICIAN") or "your care team"
        return f"""You are a warm, patient outreach voice assistant for Habitat Health's PACE program
(Program of All-Inclusive Care for the Elderly). You are calling an older adult who was
referred by their clinic. Speak slowly, in short plain sentences. Never rush. Ask one
question at a time and wait for the answer.

STEP 1 - MANDATORY IDENTITY VERIFICATION (do not skip, do not reveal the expected values)
1. Confirm you are speaking with {self.name}.
2. Ask them to state their date of birth. Expected: {_spoken_dob(self.prospect.get("DOB"))}.
3. Ask them to state their street address. Expected: {self.prospect.get("ADDRESS_STREET", "UNKNOWN")}.
If the person is not {self.name} and is not an authorized representative, or if the
date of birth or street address does not match, say: "I'm sorry, I'm not able to continue
without verifying that information. An Enrollment Specialist will follow up." Then end the call.
Do NOT discuss any health, benefit, or referral details before verification passes.

STEP 2 - WELCOME CALL DECISION TREE (only after verification)
1. Explain why you are calling: {clinician} at {dept} recommended the PACE program.
2. Explain PACE in one or two sentences: a program that helps people stay safely at home
   with a care team, transportation, meals, a day center, and medical care all coordinated
   by Habitat Health.
3. Explain the LOCK-IN PROVISION clearly: if they enroll, Habitat Health becomes their
   central care team and the sole provider for all of their Medicare and Medi-Cal services.
   They would get their care through Habitat Health and its network, not through their
   current doctors unless those doctors are part of the network. Ask if they understand that.
4. Ask whether they are open to receiving services under this model.
   - If no: thank them, tell them they can call back any time, end the call.
5. If yes, offer the next step and ask which they prefer:
   - a Nurse Home Visit, where a nurse comes to their home for a clinical assessment, or
   - a Center Tour, where they visit the Habitat Health center to see it in person.
6. Once a preference is recorded, confirm it back, say an Enrollment Specialist will call
   to schedule the exact time, thank them, and end the call.

RULES
- Never provide medical advice. Never quote costs. Never pressure.
- If they ask to speak with a person, say an Enrollment Specialist will call them back, and end the call.
- If they ask you to stop calling, apologize, confirm they will be removed, and end the call.
"""

    def build_vapi_payload(self, target_phone_e164: str) -> dict[str, Any]:
        return {
            "phoneNumberId": self.phone_number_id,
            "customer": {"number": target_phone_e164, "name": self.name},
            "assistant": {
                "name": "Habitat Health PACE Outreach",
                "firstMessage": self.first_message,
                "endCallMessage": "Thank you for your time. Goodbye.",
                "maxDurationSeconds": 600,
                "model": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "temperature": 0.3,
                    "messages": [{"role": "system", "content": self.build_system_prompt()}],
                    "tools": [{"type": "endCall"}],
                },
                "analysisPlan": {
                    "summaryPlan": {"enabled": True},
                    "structuredDataPlan": {"enabled": True, "schema": CALL_OUTCOME_SCHEMA},
                },
            },
            "metadata": {
                "prospect_name": self.name,
                "referring_department": self.prospect.get("DEPARTMENT_NAME"),
                "source": "habitat-pace-outreach",
            },
        }

    # -------------------------------------------------------------- demo mode
    def run_local_simulation(self) -> dict[str, Any]:
        phone = self.prospect.get("CLEAN_PHONE")
        shown_phone = format_phone_e164(phone) if phone else "NO VALID PHONE"
        dept = self.prospect.get("DEPARTMENT_NAME") or "your clinic"

        print("\n" + "=" * 60)
        print(f" INITIATING VOICE AI CALL TO: {self.name} ({shown_phone})")
        print("=" * 60)

        print("\n--- STAGE 1: MANDATORY PHI VERIFICATION ---")
        print(f'[VOICE AI]: "{self.first_message}"')
        print('[PROSPECT]: "Yes, this is me."')
        print('[VOICE AI]: "Could you please state your date of birth?"')
        print(f'[PROSPECT]: "{_spoken_dob(self.prospect.get("DOB"))}"')
        print("      -> [SYSTEM LOGIC]: DOB match [OK]")
        print('[VOICE AI]: "And your street address?"')
        print(f'[PROSPECT]: "{self.prospect.get("ADDRESS_STREET")}"')
        print("      -> [SYSTEM LOGIC]: Address match [OK]")

        print("\n--- STAGE 2: WELCOME CALL DECISION TREE ---")
        print(f'[VOICE AI]: "Thank you. I\'m following up because your care team at {dept}')
        print("            recommended our PACE program. Under PACE, Habitat Health becomes your")
        print("            central care team and the sole provider for all of your Medicare and")
        print('            Medi-Cal services so you can stay safely at home. Does that make sense?"')
        print('[PROSPECT]: "Yes, I understand."')
        print("      -> [SYSTEM LOGIC]: Lock-in provision acknowledged [OK]")
        print('[VOICE AI]: "Are you open to receiving services under this model?"')
        print('[PROSPECT]: "Yes, I am interested."')
        print('[VOICE AI]: "Wonderful. Would you prefer a nurse home visit for a clinical')
        print('            assessment, or a tour of our center?"')
        print('[PROSPECT]: "I\'d prefer a nurse home visit."')
        print('[VOICE AI]: "Thank you. An Enrollment Specialist will call to schedule the time."')
        print("      -> [SYSTEM LOGIC]: Preference recorded -> NURSE_HOME_VISIT. Ending call.")

        return {
            "mode": "demo",
            "prospect_name": self.name,
            "phone_called": shown_phone,
            "call_timestamp": datetime.now().isoformat(timespec="seconds"),
            "identity_verified": True,
            "lock_in_acknowledged": True,
            "interested": True,
            "next_step_preference": "NURSE_HOME_VISIT",
            "final_outcome": "SCHEDULED_NURSE_HOME_VISIT",
            "crm_action": "Create Salesforce task: ES to schedule nurse home visit",
        }

    # -------------------------------------------------------------- live mode
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def resolve_target_phone(self, target_override_phone: str | None) -> str | None:
        """Prefer an explicit test number; fall back to the prospect's primary phone."""
        if target_override_phone:
            digits = normalize_phone(target_override_phone)
            if not digits:
                print(f"\n[ERROR]: --test-phone '{target_override_phone}' is not a valid 10-digit US number.")
                return None
            return format_phone_e164(digits)
        if self.prospect.get("CLEAN_PHONE"):
            return format_phone_e164(str(self.prospect["CLEAN_PHONE"]))
        print("\n[ERROR]: Prospect has no valid phone number and no --test-phone was given.")
        return None

    def preflight(self, target_phone_e164: str) -> bool:
        """Validate credentials and the Vapi number before spending a call."""
        if not self.api_key or not self.phone_number_id:
            print("\n[ERROR]: Missing VAPI_API_KEY or VAPI_PHONE_NUMBER_ID.")
            print("         Copy .env.example to .env or export them in your shell.")
            return False

        try:
            resp = requests.get(
                f"{VAPI_BASE_URL}/phone-number/{self.phone_number_id}",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            print(f"\n[ERROR]: Could not reach Vapi: {exc}")
            return False

        if resp.status_code == 401:
            print("\n[ERROR]: Vapi rejected VAPI_API_KEY (401). Use the *Private* key from the dashboard.")
            return False
        if resp.status_code == 404:
            print(f"\n[ERROR]: No phone number with id {self.phone_number_id} (404).")
            print("         List yours with: curl -H 'Authorization: Bearer $VAPI_API_KEY' https://api.vapi.ai/phone-number")
            return False
        if not resp.ok:
            print(f"\n[ERROR]: Vapi phone-number lookup failed ({resp.status_code}): {resp.text}")
            return False

        info = resp.json()
        vapi_number = info.get("number")
        print(f"[VAPI PREFLIGHT]: Caller ID {vapi_number} ({info.get('provider')}, status={info.get('status')})")
        if vapi_number and vapi_number == target_phone_e164:
            print(f"\n[ERROR]: Target {target_phone_e164} is your own Vapi number. A number cannot call itself.")
            print("         Pass --test-phone with your personal cell instead.")
            return False
        return True

    def trigger_live_vapi_call(
        self, target_override_phone: str | None = None, dry_run: bool = False
    ) -> dict[str, Any] | None:
        target = self.resolve_target_phone(target_override_phone)
        if not target:
            return None

        payload = self.build_vapi_payload(target)

        if dry_run:
            print(f"\n[DRY RUN]: Would POST {VAPI_BASE_URL}/call/phone for {self.name} -> {target}")
            return {"mode": "dry_run", "target": target, "payload": payload}

        if not self.preflight(target):
            return None

        print(f"\n[VAPI OUTBOUND]: Placing live call to {target} for prospect {self.name}...")
        try:
            resp = requests.post(
                f"{VAPI_BASE_URL}/call/phone",
                json=payload,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            print(f"[VAPI ERROR]: Request failed: {exc}")
            return None

        if resp.status_code not in (200, 201):
            print(f"[VAPI ERROR] ({resp.status_code}): {resp.text}")
            return None

        body = resp.json()
        call_id = body.get("id")
        print(f"[VAPI SUCCESS]: Call queued. Call ID: {call_id}  status={body.get('status')}")
        print("                Your phone should ring within a few seconds.")
        print(f"                After hanging up, fetch the outcome with: python app.py --fetch-call {call_id}")
        return {
            "mode": "live",
            "call_id": call_id,
            "prospect_name": self.name,
            "phone_called": target,
            "call_timestamp": datetime.now().isoformat(timespec="seconds"),
            "status": body.get("status"),
        }


def fetch_call_outcome(call_id: str) -> dict[str, Any] | None:
    """Pull the finished call's transcript and structured outcome from Vapi."""
    api_key = os.getenv("VAPI_API_KEY")
    if not api_key:
        print("\n[ERROR]: VAPI_API_KEY is required to fetch a call.")
        return None
    try:
        resp = requests.get(
            f"{VAPI_BASE_URL}/call/{call_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        print(f"\n[ERROR]: Could not reach Vapi: {exc}")
        return None
    if not resp.ok:
        print(f"\n[VAPI ERROR] ({resp.status_code}): {resp.text}")
        return None

    call = resp.json()
    analysis = call.get("analysis") or {}
    return {
        "mode": "live",
        "call_id": call.get("id"),
        "status": call.get("status"),
        "ended_reason": call.get("endedReason"),
        "started_at": call.get("startedAt"),
        "ended_at": call.get("endedAt"),
        "cost_usd": call.get("cost"),
        "customer_number": (call.get("customer") or {}).get("number"),
        "summary": analysis.get("summary"),
        "structured_outcome": analysis.get("structuredData"),
        "transcript": call.get("transcript"),
        "recording_url": call.get("recordingUrl"),
    }
