"""
Habitat Health - Referral Triage & Voice AI Outreach Copilot.

Default run (Demo Mode, no API key):
    python app.py

Live outbound call through Vapi to YOUR phone:
    python app.py --live-vapi --test-phone "+1XXXXXXXXXX"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from pace_outreach.triage import load_and_triage_referrals, non_english_handoffs
from pace_outreach.voice_agent import VoiceAIOutreachAgent, fetch_call_outcome

load_dotenv()

LOG_DIR = Path("logs")


def write_audit_log(record: dict) -> Path:
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = LOG_DIR / f"call-{record.get('mode', 'run')}-{stamp}.json"
    path.write_text(json.dumps(record, indent=2, default=str))
    return path


def pick_prospect(queue, name: str | None):
    if not name:
        return queue.iloc[0].to_dict()
    match = queue[queue["FULL_NAME"].str.lower() == name.strip().lower()]
    if match.empty:
        print(f"\n[ERROR]: '{name}' is not in the callable English queue. Choose from:")
        for n in queue["FULL_NAME"]:
            print(f"   - {n}")
        sys.exit(1)
    return match.iloc[0].to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Habitat Health PACE Referral Triage & Voice AI Copilot",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--referrals", default="data/referrals_consent.csv", help="Path to referral CSV")
    parser.add_argument("--prospect", help="Call a specific prospect by full name instead of the top of the queue")
    parser.add_argument("--live-vapi", action="store_true", help="Place a real outbound call through Vapi")
    parser.add_argument(
        "--test-phone",
        default=os.getenv("VAPI_TEST_PHONE"),
        help="Your personal phone (E.164, e.g. +19165551234). Live calls go here instead of the prospect's number.",
    )
    parser.add_argument("--dry-run", action="store_true", help="With --live-vapi: print the Vapi payload, do not call")
    parser.add_argument("--fetch-call", metavar="CALL_ID", help="Fetch transcript + structured outcome for a finished Vapi call")
    parser.add_argument("--show-handoffs", action="store_true", help="Also list non-English referrals routed to a human OS")
    args = parser.parse_args()

    print("=" * 60)
    print(" Habitat Health: Referral Triage & Voice AI Outreach Engine")
    print("=" * 60)

    if args.fetch_call:
        outcome = fetch_call_outcome(args.fetch_call)
        if outcome is None:
            sys.exit(1)
        print("\n--- VAPI CALL OUTCOME ---")
        print(json.dumps(outcome, indent=2, default=str))
        print(f"\nAudit log written to {write_audit_log(outcome)}")
        return

    # 1. Triage
    print(f"\n[1/2] Triaging referral queue from: {args.referrals}")
    queue = load_and_triage_referrals(args.referrals)
    callable_queue = queue[queue["CALLABLE"]]
    print(f"      {len(queue)} English-language referrals, {len(callable_queue)} callable with consent.\n")

    print("Prioritized call queue:")
    for _, row in queue.iterrows():
        phone = f"+1{row['CLEAN_PHONE']}" if row["CLEAN_PHONE"] else "NO VALID PHONE"
        flag = "" if row["CALLABLE"] else "  [HOLD]"
        print(f" - [{int(row['PRIORITY_SCORE']):>3} pts] {row['FULL_NAME']:<20} {phone:<14} {row['ROUTING_REASONS']}{flag}")

    if args.show_handoffs:
        handoffs = non_english_handoffs(args.referrals)
        print(f"\nRouted to human Outreach Specialist + Propio interpreter ({len(handoffs)}):")
        for _, row in handoffs.iterrows():
            print(f" - {row['FULL_NAME']:<20} {row['PREFERRED_LANGUAGE']:<10} {row['DEPARTMENT_NAME']}")

    if callable_queue.empty:
        print("\n[2/2] Nothing callable in this batch.")
        return

    # 2. Voice AI call for the chosen prospect
    prospect = pick_prospect(callable_queue, args.prospect)
    agent = VoiceAIOutreachAgent(prospect)

    if args.live_vapi:
        label = "DRY RUN" if args.dry_run else "LIVE"
        print(f"\n[2/2] Launching {label} outbound call via Vapi for {prospect['FULL_NAME']}...")
        if not args.test_phone and not args.dry_run:
            print("\n[WARN]: No --test-phone given. The call will go to the prospect's number from the")
            print(f"        referral file (+1{prospect['CLEAN_PHONE']}). For testing, pass your own cell.")
        result = agent.trigger_live_vapi_call(target_override_phone=args.test_phone, dry_run=args.dry_run)
        if result is None:
            sys.exit(1)
        if args.dry_run:
            print(json.dumps(result["payload"], indent=2))
        else:
            print(f"\nAudit log written to {write_audit_log(result)}")
    else:
        print(f"\n[2/2] Launching Voice AI call (Demo Mode simulation) for {prospect['FULL_NAME']}...")
        call_log = agent.run_local_simulation()
        print("\n--- GENERATED AUDIT LOG (JSON) ---")
        print(json.dumps(call_log, indent=2))
        print(f"\nAudit log written to {write_audit_log(call_log)}")


if __name__ == "__main__":
    main()
