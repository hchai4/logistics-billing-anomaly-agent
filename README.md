# Logistics Billing Anomaly Agent

[![CI](https://github.com/hchai4/logistics-billing-anomaly-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/hchai4/logistics-billing-anomaly-agent/actions/workflows/ci.yml)

Freight-audit system that cross-examines warehouse scale scans, UPS EDI 210 invoices, and unstructured portal/email PDFs. Overcharges are quantified in PostgreSQL by dbt, then surfaced in a Streamlit HITL workflow that drafts carrier dispute letters.

## Quick start

Needs Docker Desktop and an OpenAI key (Tab 1 extract only).

```bash
git clone https://github.com/hchai4/logistics-billing-anomaly-agent.git
cd logistics-billing-anomaly-agent
cp .env.example .env
# set OPENAI_API_KEY in .env — leave DATABASE_URL as the example
docker compose up -d --build
```

| Service | URL |
|---|---|
| API health | http://127.0.0.1:8000/health |
| Streamlit UI | http://localhost:8501 |
| Airflow | http://localhost:8080 |
| Postgres (from your Mac) | `localhost:5432` — user/password/db `postgres` / `postgres` / `logistics_db` |

Compose starts the API, the UI, Airflow, **and** Postgres, published on host port **5432**. If another Postgres is already using 5432, stop it first. Inside the Compose network the DB host is `postgres:5432`; the compose file sets `DATABASE_URL` for the containers. The `DATABASE_URL` in `.env.example` (`localhost:5432`) is the same port, for the host venv and for tools on your Mac.

`docker/init.sql` creates empty schemas `source_enterprise` and `analytics_marts`. It does not load invoices or dbt marts.

### What you should see

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"healthy","service":"Logistics AI Agent API"}
```

`docker compose ps`: `postgres` and `api` healthy, `ui` and `airflow` running.

| Surface | After compose only |
|---|---|
| Sidebar | PostgreSQL connected, FastAPI online |
| Tab 1 | Paste a short invoice and extract (uses the API key) |
| Tab 2 | SQL error until `billing_warehouse` has built the mart and the anomaly detector has written the audit table |

Stop the stack with `docker compose down`. The `pgdata` volume stays.

## Architecture

Four layers, each with a single job: land independent evidence, sanitize it, reconcile it, then act on the result.

```mermaid
flowchart LR
  subgraph sources [Source]
    WMS[WMS scans]
    EDI[UPS EDI 210]
    PDF[Portal / email PDFs]
  end

  subgraph ingest [Ingestion and privacy]
    PII[Local PII redaction]
    LLM[Instructor / Pydantic extract]
    MATH[Printed total + Python math check]
  end

  subgraph warehouse [Warehouse]
    RAW[source_enterprise]
    DBT[dbt staging / intermediate / marts]
    GATES[5 deterministic gates]
  end

  subgraph action [Action]
    Z[Z-score flags]
    HITL[Streamlit HITL dispute letter]
  end

  WMS --> RAW
  EDI --> RAW
  PDF --> PII --> LLM --> MATH --> RAW
  RAW --> DBT --> GATES --> Z --> HITL
```

```mermaid
flowchart LR
  UI[Streamlit :8501] --> API[FastAPI :8000]
  API --> PG[Postgres :5432]
```

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
tests/                     Unit tests, e2e pipeline, and live LLM extraction evals
data/                      Fixture generators (PDFs/CSVs are gitignored)
docker/init.sql            Empty schema bootstrap for Compose Postgres
airflow/Dockerfile         Airflow image with a separate dbt virtualenv
airflow/dags/              billing_warehouse DAG: seed, dbt seed, dbt run, dbt test, flag_anomalies
```

## Warehouse DAG (Airflow)

Compose starts Airflow at http://localhost:8080. The DAG `billing_warehouse` is the scheduled path for the demo warehouse. It runs five tasks, in order, against the Compose database:

1. `seed_source_tables` reloads the raw `source_enterprise` tables. Each run receives `LOGICAL_DATE` (`{{ ds }}`). The seeder drops and reloads every raw table, so a second run does not duplicate rows.
2. `dbt_seed` loads the contract CSVs.
3. `dbt_run` builds `analytics_marts.fct_reconciliation_marts`.
4. `dbt_test` runs the dbt singular tests.
5. `flag_anomalies` runs `python -m src.analytics.anomaly_detector` and writes `source_enterprise.audit_flagged_exceptions`.

Tab 2 joins that audit table, so the page stays empty until `flag_anomalies` succeeds. `dbt_test` can be red while `flag_anomalies` still runs: that task uses `trigger_rule="all_done"`.

Every task retries twice, two minutes apart. `flag_anomalies` has a one-hour SLA. A failed task logs `ALERT <dag_id>.<task_id> failed for logical date <ds>`.

The schedule is `@daily` (midnight UTC) with `catchup=False`. The scheduler does not create old days. Trigger once from the UI, or backfill a range on purpose:

```bash
docker compose exec airflow airflow dags backfill \
  --start-date 2026-10-15 \
  --end-date 2026-10-16 \
  billing_warehouse
```

The login is `admin`. A new container prints a new password:

```bash
docker compose logs airflow | grep password
```

Airflow runs dbt from the sibling repo, mounted at `/opt/dbt`. Clone that repo next to this one before `docker compose up`:

```bash
cd ..
git clone https://github.com/hchai4/logistics-billing-anomaly-agent-dbt.git
```

The host commands in the next section are the fallback when the Airflow service is not running.

## Load demo warehouse data (host fallback, Tab 2)

Use this when you are not running the Airflow service. Tab 2 reads `analytics_marts.fct_reconciliation_marts` and also joins `source_enterprise.audit_flagged_exceptions`. The mart comes from dbt. The audit table comes from the anomaly detector. Both run on your Mac, against the Compose database on port **5432**.

Compose must already be up. Seed the raw tables from a **host** venv:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm

DATABASE_URL=postgresql://postgres:postgres@localhost:5432/logistics_db \
  python data/generate_data.py
```

Clone the sibling dbt repo and build the contract seeds plus the mart. Its profile already defaults to `localhost:5432`. Use that repo's venv and `dbt-core`, not dbt Fusion, and not the billing venv above (that venv does not install dbt):

```bash
cd ..
git clone https://github.com/hchai4/logistics-billing-anomaly-agent-dbt.git
cd logistics-billing-anomaly-agent-dbt
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
dbt seed
dbt run
```

Back in this repo, write the audit table Tab 2 joins. `dbt run` alone leaves that table missing, and the page still errors:

```bash
cd ../logistics-billing-anomaly-agent
source venv/bin/activate
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/logistics_db \
  python -m src.analytics.anomaly_detector
```

Refresh Tab 2 for flagged packages and the dispute-letter flow.

## Host venv (optional, no Compose)

If you already have Postgres on `localhost:5432` and do not want Docker:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
cp .env.example .env   # set OPENAI_API_KEY; DATABASE_URL stays localhost:5432
```

Apply `docker/init.sql` if the schemas are missing, then:

```bash
python -m src.api.main          # http://127.0.0.1:8000
streamlit run app/app.py        # http://localhost:8501
```

## Tests

Offline unit tests (PII masker, printed-total overlay, math verifier, anomaly detector, e2e PDF extract). They do not call OpenAI. Missing sample PDFs are generated during the e2e test.

```bash
python -m spacy download en_core_web_sm
pytest -q
black --check src app tests
flake8 src app tests
```

`tests/test_agent_evals.py` is a **live** extraction eval (`pytest -m live`). Default `pytest` and GitHub Actions skip it, so CI needs no API key.

| Case | What it probes | Math check |
|---|---|---|
| `clean_standard_invoice` | Clean UPS line: tracking, weight, printed total | pass (`40 + 5 = 45`) |
| `noisy_spaces_and_casing` | Spaced / mixed-case FedEx tracking (`1z 222 222` → `1Z222222`) | pass |
| `arithmetic_hallucination_invoice` | Printed total disagrees with components (`20 + 3 ≠ 35`); model must copy `$35.00`, verifier must fail | fail |

```bash
pytest -q -m live tests/test_agent_evals.py
```

GitHub Actions on each PR runs lint, offline tests, and `docker build` only.
