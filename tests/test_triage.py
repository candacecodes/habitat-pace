from pathlib import Path

import pandas as pd

from pace_outreach.triage import load_and_triage_referrals, non_english_handoffs, normalize_phone
from pace_outreach.voice_agent import VoiceAIOutreachAgent

DATA = Path(__file__).resolve().parent.parent / "data" / "referrals_consent.csv"


def test_normalize_phone_handles_referral_formats():
    assert normalize_phone("(916) 472-5389 (H)") == "9164725389"
    assert normalize_phone("916-839-1431(M)") == "9168391431"
    assert normalize_phone("+1 916 472 5389") == "9164725389"
    assert normalize_phone("(000) 000-0000 (H)") is None
    assert normalize_phone(float("nan")) is None
    assert normalize_phone("12345") is None


def test_queue_is_english_only_and_callable_first():
    queue = load_and_triage_referrals(str(DATA))
    assert set(queue["LANGUAGE_NORM"]) == {"ENGLISH"}
    assert queue["CALLABLE"].all()
    assert queue.iloc[0]["FULL_NAME"] == "Estelle Vasquez"
    assert queue.iloc[0]["CLEAN_PHONE"] == "9164725389"
    assert (queue["PRIORITY_SCORE"] == 55).all()


def test_non_english_routed_to_human():
    handoffs = non_english_handoffs(str(DATA))
    assert len(handoffs) == 10
    assert "Curtis Dunmore" in set(handoffs["FULL_NAME"])


def test_missing_consent_is_held(tmp_path):
    df = pd.read_csv(DATA, dtype=str)
    df.loc[df["LAST_NAME"] == "Vasquez", "DOTPHRASE_RESPONSE"] = "No"
    path = tmp_path / "ref.csv"
    df.to_csv(path, index=False)
    queue = load_and_triage_referrals(str(path))
    estelle = queue[queue["FULL_NAME"] == "Estelle Vasquez"].iloc[0]
    assert not estelle["CALLABLE"]
    assert queue.iloc[0]["FULL_NAME"] != "Estelle Vasquez"


def test_vapi_payload_contains_verification_targets(monkeypatch):
    monkeypatch.setenv("VAPI_API_KEY", "test-key")
    monkeypatch.setenv("VAPI_PHONE_NUMBER_ID", "test-phone-id")
    prospect = load_and_triage_referrals(str(DATA)).iloc[0].to_dict()
    agent = VoiceAIOutreachAgent(prospect)
    payload = agent.build_vapi_payload("+19165550100")
    prompt = payload["assistant"]["model"]["messages"][0]["content"]
    assert payload["phoneNumberId"] == "test-phone-id"
    assert payload["customer"]["number"] == "+19165550100"
    assert "Estelle Vasquez" in payload["assistant"]["firstMessage"]
    assert "July 2, 1948" in prompt
    assert "2125 Del Paso Blvd" in prompt
    assert "LOCK-IN PROVISION" in prompt
    assert payload["assistant"]["analysisPlan"]["structuredDataPlan"]["enabled"] is True


def test_refuses_to_call_own_vapi_number(monkeypatch):
    monkeypatch.setenv("VAPI_API_KEY", "test-key")
    monkeypatch.setenv("VAPI_PHONE_NUMBER_ID", "test-phone-id")

    class FakeResp:
        status_code = 200
        ok = True

        def json(self):
            return {"number": "+19388370246", "provider": "vapi", "status": "active"}

    monkeypatch.setattr("pace_outreach.voice_agent.requests.get", lambda *a, **k: FakeResp())
    agent = VoiceAIOutreachAgent({"FULL_NAME": "Test Person", "DOB": "01/01/1950", "ADDRESS_STREET": "1 Main St"})
    assert agent.preflight("+19388370246") is False
    assert agent.preflight("+19165550100") is True
