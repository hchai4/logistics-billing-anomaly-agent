# Logistics Billing Anomaly Agent

Freight-audit system that cross-examines warehouse scale scans, UPS EDI 210 invoices, and unstructured portal/email PDFs. Overcharges are quantified in PostgreSQL by dbt, then surfaced in a Streamlit HITL workflow that drafts carrier dispute letters.

## Architecture

Four layers, each with a single job: land independent evidence, sanitize it, reconcile it, then act on the result.

```
Source ──► Ingestion & Privacy ──► Warehouse (PostgreSQL + dbt) ──► Action
   │              │                         │                          │
 WMS scans     PII redaction          source_enterprise          Z-score flags
 EDI 210       Instructor/Pydantic    analytics_marts            Dispute letters
 Portal PDFs   Math verification      5 deterministic gates      Streamlit HITL
```

### Source Layer

Three independent streams. They never overwrite each other; they are compared.

| Stream | What it answers | Lands in |
|---|---|---|
| **WMS physical ground truth** | What did we actually ship? Certified conveyor-scale weight, cubing-laser dimensions, origin/destination ZIPs, dock departure. | `source_enterprise.raw_wms_package_scans` |
| **UPS EDI 210 structured feed** | What did the carrier bill on the automated weekly invoice? Weight, base rate, fuel, line total. | `source_enterprise.raw_edi_ups_invoices` |
| **PDF / email unstructured invoices** | What did a later portal adjustment or emailed PDF demand? Same billed fields, extracted by the LLM agent. | `source_enterprise.raw_portal_extracted_invoices` |

The join key across all three is `tracking_id`. Contract tariffs (rate card, zone matrix, fuel index, accessorial schedule) are version-controlled as dbt seeds rather than ingested feeds — a tariff is a legal document, not an operational dump.

The PDF is **not** a fourth pillar. Physical / Contract / Billed is the 3-way audit. EDI and portal PDFs are two *channels* of the billed pillar, unioned with `UNION ALL` so a shipment billed twice stays visible.

### Ingestion & Privacy Layer

Unstructured invoices never reach an external LLM until PII is stripped and the output is forced into a typed schema.

1. **PDF text extraction** (`src/utils/pdf_extractor.py`) — `pypdf` reads the embedded text layer. Scanned-image PDFs fail closed rather than silently OCR.
2. **Local PII redaction** (`src/privacy/pii_masker.py`) — regex plus spaCy NER replace emails, phones, person names, and street addresses with `[REDACTED_*]` tokens *before* the OpenAI call.
3. **Schema enforcement** (`src/agent/llm_agent.py`) — `instructor` wraps `gpt-4o-mini` with `response_model=CarrierInvoiceExtraction`. Invalid JSON is retried until it satisfies the Pydantic model (`src/agent/schemas.py`).
4. **Printed-total overlay** (`src/agent/printed_amounts.py`) — regex recovers the carrier's printed `Total Item Charge` so a model that "fixed" the arithmetic cannot erase the fraud signal.
5. **Deterministic math check** (`src/agent/math_verifier.py`) — Python compares `base + fuel + tax` to the printed total (±$0.01). No model involved.

The FastAPI service (`src/api/main.py`) exposes the same pipeline at `POST /api/extract` and dispute generation at `POST /api/generate-dispute`. Tab 1 of the Streamlit app is the interactive equivalent.

### Warehouse Layer

PostgreSQL isolates landing data from analytical output:

| Schema | Role |
|---|---|
| `source_enterprise` | Raw landing: WMS scans, EDI lines, portal extractions, accessorial fees, HITL dispute statuses. |
| `analytics_marts` | dbt-built dimensional models. Regenerated with `dbt run`; never written by the app. |

The dbt project lives in the sibling directory `../logistics-billing-anomaly-agent-dbt` (profile `logistics_billing_audit`). Staging models type and alias each feed onto a common contract. Intermediate models resolve three pillars:

1. **Physical** — `int_package_physical_truth`: billable weight = max(scale weight, DIM weight using the *contracted* divisor), zone from the warehouse ZIP pair.
2. **Contract** — `int_contract_expectations`: rate-card lookup on that independently derived zone and weight, never on the invoice's own claims.
3. **Billed** — `int_billed_charges`: first-arriving line is primary (Gates 1, 2, 3, 5); every later line is duplicate exposure (Gate 4).

`fct_reconciliation_marts` is one row per package. Grain is the WMS scan (`FROM physical_truth LEFT JOIN billed`), so a shipped-but-uninvoiced package surfaces as `AWAITING_INVOICE` instead of disappearing. Five gates fire against that row:

| Gate | Compares | Flag |
|---|---|---|
| 1 | billed weight vs expected billable weight | `WEIGHT_INFLATION` |
| 2 | billed base rate vs rate card | `BASE_RATE_OVERCHARGE` |
| 3 | billed fees vs contract-authorized fees | `UNAUTHORIZED_*_FEE` |
| 4 | same tracking ID on 2+ invoices or channels | `DUPLICATE_BILLING_COLLISION` |
| 5 | actual vs guaranteed delivery | `SLA_DELIVERY_FAILURE_REFUND` |

Recoverable dollars are attributed once: Gate 1 explains *why* the rate bracket is wrong; Gate 2 holds the money; Gate 5 (full GSR refund) supersedes both.

### Action Layer

1. **Statistical anomaly detection** (`src/analytics/anomaly_detector.py`) — grouped Z-score on `dollar_variance` by carrier (flag if Z > 2.0), plus a channel diagnosis (EDI tariff drift vs sporadic portal adjustments). Combined with dbt `is_eligible_for_dispute` and written to `source_enterprise.audit_flagged_exceptions`.
2. **HITL dispute resolution** (`app/app.py` Tab 2) — KPI banner, date/urgency filters, bulk CSV for the carrier portal, and a per-tracking evidence card. `POST /api/generate-dispute` (or an in-process `DisputeAgent` fallback) drafts the claim letter; approval writes `DISPUTE_SENT` back to PostgreSQL.

## Project layout

```
app/app.py                 Streamlit UI (ingestion gate + audit/disputes)
src/agent/                 LLM extraction, schemas, math verifier, dispute letters
src/privacy/               Local PII redaction
src/utils/                 PDF text extraction
src/db/                    Writes extracted lines to PostgreSQL
src/analytics/             Z-score detector over the dbt mart
src/api/                   FastAPI: /api/extract, /api/generate-dispute
tests/                     Unit and e2e pipeline tests
data/                      Fixture generators (PDFs/CSVs are gitignored)
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
cp .env.example .env   # then set OPENAI_API_KEY
```

PostgreSQL must be reachable at `DATABASE_URL` (default `localhost:5432/logistics_db`) with schemas `source_enterprise` and `analytics_marts`.

Rebuild analytical marts from the dbt project (use the venv's `dbt-core`, not dbt Fusion):

```bash
cd ../logistics-billing-anomaly-agent-dbt
dbt run
```

Generate demo PDFs if you need them for tests:

```bash
python data/generate_sample_pdfs.py
```

## Run

```bash
# API
python -m src.api.main          # http://127.0.0.1:8000

# UI
streamlit run app/app.py        # http://localhost:8501
```

```bash
pytest
black src app tests
flake8 src app tests
```
