# Habitat Health - Referral Triage & Voice AI Outreach Copilot

A small, working prototype for getting
a **consented referral to a completed Welcome Call** quickly and consistently for PACE enrollment.

It does two things:

1. **Deterministic triage** of the partner referral/consent extract - normalizes contact
   data, holds anyone without explicit consent or a dialable phone, routes non-English
   speakers to a human Outreach Specialist + Propio, and ranks the rest.
2. **A Voice AI Welcome Call** for the top prospect that runs the same script every time:
   mandatory PHI verification (name, DOB, street address) *before* any program details,
   then the lock-in provision, then the next-step choice (Nurse Home Visit vs. Center Tour).
   Runs offline in **Demo Mode**, or places a **real call through [Vapi](https://vapi.ai)**
   in Live Mode.

All data in `data/` is synthetic (see `data/README_DATA.md`).

---

## Quickstart

### Demo Mode (default - no API key, no network)

```bash
pip install -r requirements.txt
python app.py
```

Output: the prioritized call queue, a scripted walkthrough of the Welcome Call for the
top prospect (Estelle Vasquez), and a JSON audit log written to `logs/`.

Useful flags:

```bash
python app.py --show-handoffs                 # also list non-English referrals routed to a human
python app.py --prospect "Lucille Xiong"      # run the call for a specific queued prospect
python -m pytest -q                           # unit tests for triage + payload construction
```

### Live Mode - real outbound call via Vapi

Vapi's free tier includes trial credit and one US phone number, which is enough to test this.

**1. Get credentials** from the [Vapi dashboard](https://dashboard.vapi.ai):

- **API Keys** -> copy your **Private** API key.
- **Phone Numbers** -> claim a free number and copy its **ID** (a UUID). Vapi needs the UUID,
  not the `+1...` string. You can also list it with:

  ```bash
  curl -s -H "Authorization: Bearer $VAPI_API_KEY" https://api.vapi.ai/phone-number
  ```

**2. Set them** - either export in your shell, or copy `.env.example` to `.env`
(git-ignored, loaded automatically):

```bash
export VAPI_API_KEY="your-private-key"
export VAPI_PHONE_NUMBER_ID="your-phone-number-uuid"
```

**3. Preview the request** without calling anyone:

```bash
python app.py --live-vapi --dry-run --test-phone "+1XXXXXXXXXX"
```

**4. Place the call to your own cell.** The synthetic prospects have fake numbers, so
`--test-phone` redirects the call to you while keeping the prospect's script
(Estelle Vasquez, DOB July 2, 1948, 2125 Del Paso Blvd):

```bash
python app.py --live-vapi --test-phone "+1XXXXXXXXXX"
```

Your phone rings within a few seconds. When you answer:

1. The agent says *"Hello, this is the Habitat Health PACE program calling. Am I speaking with Estelle Vasquez?"*
2. Say "Yes", then give the DOB (**July 2nd, 1948**) and address (**2125 Del Paso Blvd**).
   Try a wrong DOB on a second call - the agent should refuse to continue.
3. The agent explains the **lock-in provision**, asks if you are open to PACE, then offers
   **Nurse Home Visit** vs. **Center Tour** and ends the call once you choose.

**5. Pull the outcome** after you hang up (Vapi needs ~30 s to run the post-call analysis):

```bash
python app.py --fetch-call <CALL_ID>
```

This returns the transcript, Vapi's summary, and the **structured outcome**
(`identity_verified`, `lock_in_acknowledged`, `interested`, `next_step_preference`) - the
fields that would be written back to the Salesforce Opportunity.

> `--test-phone` must be **your** cell, not the Vapi number itself. The preflight check
> refuses to dial the Vapi number (a number cannot call itself) and also validates your key
> and phone-number ID before spending any credit.

---

## Write-up

### 1. Problem & scope

The case study describes referrals that "wait too long for a first call and some go cold",
and call quality that "depends on individual memory rather than a shared standard". Both
land on the same step: the first outreach call. An ES/OS is in the field half the day, so
a referral that arrives at 9 a.m. may not be dialed until the next week.

I scoped to **referral -> prioritized queue -> standardized Welcome Call -> structured
outcome**. One input file, one command, one happy path.

**Deliberately left out:**

- Salesforce / Talkdesk / Epic write-back. The audit log JSON is shaped to be the payload;
  the integration itself is plumbing I could not test without the live systems.
- Multi-attempt cadence and voicemail handling. Real outreach "usually takes multiple call
  attempts"; this prototype places one.
- Non-English calls. The agent only handles English today, so other languages are routed to
  a human with Propio rather than risk a bad translation on a consent-sensitive call.
- Reconciliation across the referral file, Salesforce exports, and state forms. It is a real
  problem (name + DOB matching with no shared ID) but a separate one.

### 2. Solution design

```
data/referrals_consent.csv
        |
        v
pace_outreach/triage.py        deterministic: normalize phone, language filter,
        |                      consent gate, priority score, stable sort
        v
pace_outreach/voice_agent.py   builds ONE call script from the prospect row
        |                                  |
        v                                  v
run_local_simulation()          trigger_live_vapi_call()  ->  POST api.vapi.ai/call/phone
(Demo Mode, offline)            (Live Mode, needs env vars)        |
        |                                                          v
        v                                       fetch_call_outcome()  <- GET /call/{id}
logs/call-*.json  <------------------------------  transcript + structured outcome
```

**Deterministic vs. AI.** Everything that is a compliance decision is code:

- Who is dialable: explicit consent present, valid 10-digit phone, English speaker.
- Who dials first: scoring rules in `triage.py` (consent +30, alternate contact +15,
  missing phone -25, missing DOB -10). Ties keep referral-file order.
- What must be verified and in what order: the prompt hard-codes "verify name, DOB, and
  street address before any program details" and tells the model not to reveal the expected
  values. The expected DOB is pre-formatted as spoken English so the model is comparing
  like with like.
- What comes back: Vapi's `structuredDataPlan` forces the post-call extraction into a fixed
  schema (`CALL_OUTCOME_SCHEMA`) with an enum for next step, so downstream code never
  parses free text.

The LLM (gpt-4o-mini via Vapi) handles only the conversation itself: pacing, rephrasing
the lock-in provision when someone does not follow, and handling "call me back later".

**Integration points & assumptions.**

- Vapi is used as the telephony + STT + LLM + TTS stack via a *transient assistant* in the
  call request, so there is nothing to configure in the Vapi dashboard beyond a key and a
  number. In production this would be a Talkdesk-adjacent dialer or a Vapi assistant with
  server-side tool calls into Salesforce.
- The referral extract's shape is taken as given; `normalize_phone` tolerates the formats
  that appear in it (`(916) 472-5389 (H)`, `916-839-1431(M)`, `(000) 000-0000`).
- Consent is inferred from the clinician's DotPhrase attestation text. A real deployment
  would key off a structured consent flag.
- Only the primary phone is dialed. Alternate contacts raise priority but are not dialed
  automatically, since many are "designated decision maker (not a legal designation)" and
  PHI disclosure rules differ.

### 3. Success criteria

Measured against the Salesforce calls export, which already has one row per attempt:

- **Time to first contact**: median hours from referral `RESPONSE_DATE` to first completed
  Welcome Call. Target: same business day for consented English referrals.
- **Welcome Call completion rate** within 72 h of referral.
- **Script adherence**: % of completed calls where `identity_verified` and
  `lock_in_acknowledged` are both true in the structured outcome. This is the quality
  standard the case study says is missing today.
- **Conversion to Nurse Home Visit** scheduled, compared with human-only outreach over the
  same period.
- **Guardrail metrics**: calls ended because verification failed, opt-out requests, and
  prospect requests for a human. These need to stay low and be reviewed weekly.

**Scaling to additional centers.** The queue is partitioned by `DEPARTMENT_NAME`
(e.g. `SAC-*` vs. `ROS-*`), so each center gets its own queue without code changes. The
call script is a template per prospect, so adding a center means adding its name and
address, not rewriting prompts. Language support becomes a routing rule plus a
language-specific transcriber/voice in `build_vapi_payload`. Running triage as a scheduled
Databricks job over the daily referral drop, with the structured outcome landing in the
same warehouse, is a straight line from what is here.

---

## Project layout

```
.
├── app.py                      CLI entry point (demo / live / dry-run / fetch-call)
├── pace_outreach/
│   ├── triage.py               deterministic referral triage + scoring
│   └── voice_agent.py          call script, demo simulation, Vapi client
├── tests/test_triage.py        unit tests (run: python -m pytest -q)
├── data/                       synthetic fixtures from the case study
│   ├── referrals_consent.csv   <- the input this prototype uses
│   ├── Central_Dashboard_-_*.xlsx, IDT_Handoff_*.docx, DHCS_*  (not used yet)
│   └── README_DATA.md
├── docs/                       case study brief + Outreach & Enrollment SOP
├── .env.example                Vapi credential template (copy to .env)
└── requirements.txt
```

## Security note

Vapi credentials are read from environment variables (or a git-ignored `.env`). The
Private API key should never be committed. If a key has been shared in plain text anywhere
(chat, email, a ticket), rotate it in the Vapi dashboard.
