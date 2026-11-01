# Case Study Data - Synthetic Fixtures

Everything in this folder is **synthetic**. No real participant or patient data is used. Treat PHI and compliance constraints as if they were real anyway.

These files mirror the shape and field values of Habitat's real systems so you can build against something realistic. You do **not** need to use all of them - pick what your chosen problem needs.

## What's here

- **`referrals_consent.csv`** - a batch of inbound referrals with contact info, consent, and referring-clinician detail (mirrors a typical partner referral/consent extract).
- **`Central_Dashboard_-_Opportunity_Export.xlsx`** - the enrollment pipeline from Salesforce: one row per prospect, with funnel dates, per-step status, and computed metrics. Note the report preamble above the header row, as in a real dashboard export.
- **`Central_Dashboard_-_Calls_Export.xlsx`** - the Salesforce call activity log: one row per call attempt, linked to the pipeline by `Opportunity ID` and `Account ID`.
- **`IDT_Handoff_*.docx`** - example clinical/social handoff forms, stored in our SharePoint.
- **`DHCS_ILOC_*.docx`** - example state Initial Level of Care certification form, stored in our SharePoint, to be submitted to the state in preserved document format.
- **`DHCS_LOC_Determination_*.docx`** - example state Level of Care determination tool, stored in our SharePoint to be submitted with certification form, completed by our Enrollment RN.
- **`Email_ILOC_Review_*.docx`** - sample internal email threads showing an ILOC-ready-for-review handoff with multiple stakeholders, including MD review.

## How they connect

- `Opportunity ID` links the pipeline export to the calls export.
- `Account ID` identifies a person across both exports.
- The referral CSV and the Word forms have **no ID column** - matching them back to the pipeline is by name + date of birth, as it is in real life.
