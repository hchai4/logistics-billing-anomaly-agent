import os
import sys
from datetime import timedelta
from pathlib import Path

# Streamlit puts app/ on sys.path, not the project root, so `src` needs help.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

from src.agent.llm_agent import InvoiceExtractionAgent
from src.utils.pdf_extractor import extract_text_from_pdf
from src.privacy.pii_masker import PIIMasker
from src.db.db_writer import insert_extracted_invoice
from src.agent.dispute_agent import DisputeAgent, DisputeRequest

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/logistics_db"
)
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

# ---------------------------------------------------------
# Page Configuration
# ---------------------------------------------------------
st.set_page_config(
    page_title="Enterprise Logistics Audit & AI Dispute Hub",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------
# Sample Test Data
# ---------------------------------------------------------
SAMPLE_PASSING_INVOICE = """UPS FREIGHT INVOICE #UPS-994112
Date: February 10, 2026
Billing Contact: Customer Sarah Smith (email: sarah@ecomm-store.com)
Shipping Address: 123 Industrial Pkwy, Los Angeles, CA 90001

Line Item 1:
Tracking Number: 1z 999 999 9999 9999 99
Billed Weight: 18.5 lbs
Base Rate: $45.00
Fuel Surcharge: $8.50
Total Item Charge: $53.50

Line Item 2:
Tracking Number: 1Z 888 888 8888 8888 88
Billed Weight: 4.0 lbs
Base Rate: $12.00
Fuel Surcharge: $2.00
Total Item Charge: $14.00"""

SAMPLE_FAILING_INVOICE = """FEDEX EXPRESS INVOICE #FDX-554201
Date: February 11, 2026
Billing Contact: Customer Michael Chang (phone: 555-432-8765)
Delivery Location: 456 Commerce Blvd, Atlanta, GA 30301

Line Item 1:
Tracking Number: 7949 1122 3344
Billed Weight: 12.0 lbs
Base Rate: $30.00
Fuel Surcharge: $5.00
Tax: $1.50
Total Item Charge: $45.00  <-- CORRUPTED: 30 + 5 + 1.50 = 36.50, NOT 45.00"""


# ---------------------------------------------------------
# Cached Resource Initializations
# ---------------------------------------------------------
@st.cache_resource
def get_agents():
    return InvoiceExtractionAgent(), PIIMasker(), DisputeAgent()


extraction_agent, pii_masker, dispute_agent = get_agents()


@st.cache_resource
def get_engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True)


# Column aliases mirror src/analytics/anomaly_detector.py so both readers of the
# mart expose the same field names to downstream code.
AUDIT_COLUMNS = (
    "tracking_id",
    "package_id",
    "invoice_number",
    "carrier_name",
    "source_channel",
    "actual_scale_weight",
    "billed_weight",
    "weight_variance_lbs",
    "expected_total_cost",
    "billed_total",
    "dollar_variance",
    "anomaly_classification",
)


def load_reconciliation_mart() -> pd.DataFrame:
    """Queries fct_reconciliation_marts joined with exception audit statuses."""
    query = """
    SELECT
        m.tracking_id,
        m.package_id,
        m.primary_invoice_number AS invoice_number,
        m.carrier_code AS carrier_name,
        m.primary_source_channel AS source_channel,
        m.scan_timestamp,
        m.actual_scale_weight_lbs AS actual_scale_weight,
        m.billed_weight_lbs AS billed_weight,
        m.weight_variance_lbs,
        m.expected_total_cost_usd AS expected_total_cost,
        -- All-lines total, not billed_total_usd: only this reconciles as
        -- billed - expected = total_variance_usd on duplicate-billing rows.
        m.billed_total_all_lines_usd AS billed_total,
        m.total_variance_usd AS dollar_variance,
        m.anomaly_codes AS anomaly_classification,
        COALESCE(m.total_recoverable_usd, 0) > 0 AS is_eligible_for_dispute,
        CASE WHEN COALESCE(e.has_dispute_sent, FALSE) THEN 'DISPUTE_SENT'
             ELSE 'PENDING_REVIEW' END AS dispute_status
    FROM analytics_marts.fct_reconciliation_marts m
    LEFT JOIN (
        -- Pre-aggregated so repeated audit rows per tracking_id cannot fan out the mart.
        SELECT tracking_id, bool_or(dispute_status = 'DISPUTE_SENT') AS has_dispute_sent
        FROM source_enterprise.audit_flagged_exceptions
        GROUP BY tracking_id
    ) e ON m.tracking_id = e.tracking_id
    ORDER BY dollar_variance DESC;
    """
    return pd.read_sql(query, con=get_engine())


def update_dispute_status_in_db(row_data: dict):
    """Updates or records dispute status as DISPUTE_SENT in PostgreSQL."""
    update_sql = text(
        """
    UPDATE source_enterprise.audit_flagged_exceptions
    SET dispute_status = 'DISPUTE_SENT', flagged_at = CURRENT_TIMESTAMP
    WHERE tracking_id = :tracking_id;
    """
    )
    insert_sql = text(
        """
    INSERT INTO source_enterprise.audit_flagged_exceptions (
        tracking_id, package_id, invoice_number, carrier_name, source_channel,
        actual_scale_weight, billed_weight, weight_variance_lbs,
        expected_total_cost, billed_total, dollar_variance,
        anomaly_classification, dispute_status, flagged_at
    ) VALUES (
        :tracking_id, :package_id, :invoice_number, :carrier_name, :source_channel,
        :actual_scale_weight, :billed_weight, :weight_variance_lbs,
        :expected_total_cost, :billed_total, :dollar_variance,
        :anomaly_classification, 'DISPUTE_SENT', CURRENT_TIMESTAMP
    );
    """
    )
    params = {col: row_data[col] for col in AUDIT_COLUMNS}
    with get_engine().begin() as conn:
        if conn.execute(update_sql, params).rowcount == 0:
            conn.execute(insert_sql, params)


# ---------------------------------------------------------
# Sidebar Navigation & System Health
# ---------------------------------------------------------
with st.sidebar:
    st.header("⚙️ System Status")

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        st.success("🟢 PostgreSQL: Connected")
    except Exception:
        st.error("🔴 PostgreSQL: Unreachable")

    # Check FastAPI Backend Health
    try:
        health_resp = requests.get(f"{API_BASE_URL}/health", timeout=1.0)
        if health_resp.status_code == 200:
            st.success("🟢 FastAPI Backend: Online")
        else:
            st.warning("🟡 FastAPI Backend: Degraded")
    except Exception:
        st.info("ℹ️ FastAPI Backend: Offline (Using Local Fallback)")

    st.divider()
    st.markdown("**Enterprise Architecture Stack:**")
    st.markdown("- **Sources:** WMS Scale Scans + EDI 210 + Portal PDFs")
    st.markdown("- **Privacy:** Ingress PII Sanitization")
    st.markdown("- **Warehouse:** PostgreSQL + dbt-core 3-Way Marts")
    st.markdown("- **Action:** Human-in-the-Loop Dispute Agent")

# ---------------------------------------------------------
# Top Header & Tabs
# ---------------------------------------------------------
st.title("📦 Enterprise Logistics Audit & Dispute Hub")
st.caption(
    "Integrated Ingestion, dbt Source-to-Source Reconciliation, and Human-in-the-Loop Claims Recovery"
)

tab_ingestion, tab_reconciliation = st.tabs(
    [
        "📥 Tab 1: Live Invoice Ingestion & Defense Gate",
        "📊 Tab 2: Enterprise 3-Way Audit Mart & Disputes",
    ]
)


# =========================================================
# TAB 1: LIVE INVOICE INGESTION & DEFENSE GATE
# =========================================================
with tab_ingestion:
    st.subheader("Ingest Unstructured Carrier Invoices (Portal PDF / Email)")
    st.markdown(
        "Upload carrier bills to sanitize customer PII, enforce Pydantic schemas, and run math checks before warehouse ingestion."
    )

    col_btn1, col_btn2 = st.columns([1, 1])
    with col_btn1:
        if st.button("Load Valid Invoice Sample", use_container_width=True):
            st.session_state["invoice_input"] = SAMPLE_PASSING_INVOICE
    with col_btn2:
        if st.button("Load Corrupted Invoice Sample", use_container_width=True):
            st.session_state["invoice_input"] = SAMPLE_FAILING_INVOICE

    if "invoice_input" not in st.session_state:
        st.session_state["invoice_input"] = SAMPLE_PASSING_INVOICE

    uploaded_file = st.file_uploader(
        label="Upload Carrier Invoice (.pdf or .txt):",
        type=["pdf", "txt"],
        help="Upload an invoice to trigger ingress PII redaction.",
    )

    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith(".pdf"):
                raw_extracted_text = extract_text_from_pdf(uploaded_file)
            else:
                raw_extracted_text = uploaded_file.read().decode("utf-8")

            # Ingress PII Sanitization
            masked_text, pii_stats = pii_masker.mask(raw_extracted_text)
            st.session_state["invoice_input"] = masked_text
            st.success(
                f"📄 Processed '{uploaded_file.name}'! (Scrubbed {sum(pii_stats.values())} PII fields at UI boundary)"
            )
        except Exception as e:
            st.error(f"Failed to read file: {e}")

    invoice_text = st.text_area(
        label="Active Invoice Content (Pre-processed & Sanitized):",
        value=st.session_state["invoice_input"],
        height=180,
    )

    if st.button(
        "🚀 Run AI Extraction & Math Verification",
        type="primary",
        use_container_width=True,
    ):
        if invoice_text is None or not invoice_text.strip():
            st.warning("⚠️ Please provide invoice text to analyze.")
        else:
            with st.spinner(
                "Extracting Pydantic Schema & Running Deterministic Math Verification..."
            ):
                try:
                    extracted_data, pii_stats, math_report = extraction_agent.extract(
                        invoice_text
                    )
                    st.session_state["latest_extraction"] = extracted_data
                    st.session_state["latest_math_report"] = math_report
                    st.session_state["latest_pii_stats"] = pii_stats
                except Exception as e:
                    st.error(f"Extraction failed: {str(e)}")

    # Render extraction results if available
    if "latest_extraction" in st.session_state:
        extracted_data = st.session_state["latest_extraction"]
        math_report = st.session_state["latest_math_report"]
        pii_stats = st.session_state["latest_pii_stats"]

        st.divider()

        # Status Badges
        if math_report.is_passed:
            st.success("### ✅ DEFENSE 2 PASSED: 100% Arithmetic Integrity Verified")
        else:
            st.error(
                f"### ❌ DEFENSE 2 FAILED: {math_report.failed_items_count} Arithmetic Discrepancy Detected"
            )

        # Metric Cards
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Invoice #", extracted_data.invoice_number)
        c2.metric("Carrier", extracted_data.carrier_name)
        c3.metric(
            "Math Audit",
            "PASSED" if math_report.is_passed else "FAILED",
            delta=(
                "Verified"
                if math_report.is_passed
                else f"-{math_report.failed_items_count} Errors"
            ),
            delta_color="normal" if math_report.is_passed else "inverse",
        )
        c4.metric("PII Redacted", f"{sum(pii_stats.values())} fields")

        # Detailed Breakdown Table
        table_rows = []
        for item, m_res in zip(extracted_data.items, math_report.item_results):
            table_rows.append(
                {
                    "Tracking ID": item.tracking_id,
                    "Billed Weight (lbs)": item.billed_weight,
                    "Base Rate ($)": f"${item.base_charge:.2f}",
                    "Fuel ($)": f"${item.fuel_surcharge:.2f}",
                    "Billed Total ($)": f"${item.grand_total:.2f}",
                    "Calculated Total ($)": f"${m_res.calculated_total:.2f}",
                    "Variance ($)": f"${m_res.variance:.2f}",
                    "Status": "✅ PASSED" if m_res.is_valid else "❌ FAILED",
                }
            )
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True)

        # Database Ingestion Action
        st.markdown("#### 💾 Persist Verified Data to Warehouse")
        col_db1, col_db2 = st.columns([2, 1])
        with col_db1:
            st.info(
                "Write these extracted invoice items into `source_enterprise.raw_portal_extracted_invoices` for downstream dbt reconciliation."
            )
        with col_db2:
            if st.button(
                "📥 Save & Ingest to Database",
                type="secondary",
                use_container_width=True,
            ):
                try:
                    rows_inserted = insert_extracted_invoice(
                        extracted_data, source_label="PORTAL_PDF_AI"
                    )
                    st.success(
                        f"✅ Successfully ingested {rows_inserted} line items into PostgreSQL!"
                    )
                    st.toast(
                        "Database updated! Run 'dbt run' to refresh analytical marts.",
                        icon="🚀",
                    )
                except Exception as e:
                    st.error(f"Database write failed: {e}")


# =========================================================
# TAB 2: ENTERPRISE 3-WAY AUDIT MART & DISPUTES
# =========================================================
with tab_reconciliation:
    st.subheader("3-Way Reconciliation Mart (`fct_reconciliation_marts`)")
    st.markdown(
        "Cross-examining **WMS Physical Ground Truth**, **UPS EDI 210 Billing**, and **AI-Extracted Invoices** via dbt dimensional models."
    )

    # Carrier contracts allow 30 days from the scan to file an overcharge claim, and
    # a claim inside the last week of that window is effectively use-it-or-lose-it.
    DISPUTE_WINDOW_DAYS = 30
    CRITICAL_WINDOW_DAYS = 7
    # Above this variance a claim is worth filing without line-by-line review.
    HIGH_CONFIDENCE_CLAIM_USD = 15.00

    URGENCY_CRITICAL = f"CRITICAL (< {CRITICAL_WINDOW_DAYS} Days)"
    URGENCY_ACTIVE = f"ACTIVE ({CRITICAL_WINDOW_DAYS + 1}-{DISPUTE_WINDOW_DAYS} Days)"
    URGENCY_SENT = "DISPUTE_SENT"

    try:
        df_mart = load_reconciliation_mart()

        if df_mart.empty:
            st.warning(
                "⚠️ No reconciliation records found. Please run 'dbt run' in your terminal."
            )
        else:
            # -------------------------------------------------
            # Dispute aging against the contractual filing window
            # -------------------------------------------------
            # Aging is measured against the current UTC calendar date, the same clock
            # the carrier contract is written against.
            today_date = pd.Timestamp.now(tz="UTC").date()

            audit_df = df_mart.copy()
            audit_df["scan_timestamp"] = pd.to_datetime(audit_df["scan_timestamp"])
            audit_df["scan_date"] = audit_df["scan_timestamp"].dt.normalize()
            audit_df["days_since_scan"] = (
                pd.Timestamp(today_date) - audit_df["scan_date"]
            ).dt.days
            audit_df["days_remaining"] = (
                DISPUTE_WINDOW_DAYS - audit_df["days_since_scan"]
            )

            # Assigned in ascending order of precedence: an already-filed claim is no
            # longer aging, so DISPUTE_SENT overwrites whatever urgency it would carry.
            audit_df["aging_urgency"] = URGENCY_ACTIVE
            audit_df.loc[
                audit_df["days_remaining"] <= CRITICAL_WINDOW_DAYS, "aging_urgency"
            ] = URGENCY_CRITICAL
            audit_df.loc[
                audit_df["dispute_status"] == "DISPUTE_SENT", "aging_urgency"
            ] = URGENCY_SENT

            scan_days = pd.Series(audit_df["scan_date"]).dt.date.dropna()
            if scan_days.empty:
                data_min_date = data_max_date = today_date
            else:
                data_min_date = scan_days.min()
                data_max_date = scan_days.max()

            # Surface the outcome of a write that ran just before a rerun.
            flash = st.session_state.pop("audit_flash", None)
            if flash:
                st.success(flash)
                st.balloons()
            flash_error = st.session_state.pop("audit_flash_error", None)
            if flash_error:
                st.error(flash_error)

            # -------------------------------------------------
            # Executive KPI banner
            # -------------------------------------------------
            overcharges = pd.DataFrame(
                audit_df[audit_df["dollar_variance"] > 0], copy=False
            )
            total_leakage = pd.Series(overcharges["dollar_variance"]).sum()
            overcharge_count = len(overcharges)

            critical_open = audit_df[
                (audit_df["is_eligible_for_dispute"])
                & (audit_df["dispute_status"] != "DISPUTE_SENT")
                & (audit_df["days_remaining"] <= CRITICAL_WINDOW_DAYS)
            ]
            critical_count = len(critical_open)

            disputes_sent = pd.DataFrame(
                audit_df[audit_df["dispute_status"] == "DISPUTE_SENT"], copy=False
            )
            claimed_total = pd.Series(disputes_sent["dollar_variance"]).sum()

            with st.container(horizontal=True):
                st.metric("Audited shipments", f"{len(audit_df):,}", border=True)
                st.metric(
                    "Identified leakage",
                    f"${total_leakage:,.2f}",
                    delta=f"{overcharge_count:,} overcharges",
                    delta_color="off",
                    delta_arrow="off",
                    border=True,
                )
                st.metric(
                    f"Critical expiring (< {CRITICAL_WINDOW_DAYS}d)",
                    f"{critical_count:,}",
                    delta="Filing window closing" if critical_count else "None at risk",
                    delta_color="inverse" if critical_count else "off",
                    delta_arrow="off",
                    border=True,
                )
                st.metric(
                    "Disputes submitted",
                    f"{len(disputes_sent):,}",
                    delta=f"${claimed_total:,.2f} claimed",
                    delta_color="off",
                    delta_arrow="off",
                    border=True,
                )

            st.divider()

            # -------------------------------------------------
            # Filter controls: row 1 presets + date range
            # -------------------------------------------------
            preset_30_start = today_date - timedelta(days=DISPUTE_WINDOW_DAYS - 1)
            preset_7_start = today_date - timedelta(days=CRITICAL_WINDOW_DAYS - 1)
            preset_mtd_start = today_date.replace(day=1)

            def _set_date_range(start_date, end_date) -> None:
                """Preset buttons write straight to the date widget's own state."""
                st.session_state["audit_date_range"] = (start_date, end_date)

            if "audit_date_range" not in st.session_state:
                # "Last 30 days" is the intended default, but a warehouse whose most
                # recent scan predates that window would open on an empty grid, so
                # fall back to the full loaded span instead of showing nothing.
                if data_max_date >= preset_30_start:
                    st.session_state["audit_date_range"] = (preset_30_start, today_date)
                else:
                    st.session_state["audit_date_range"] = (
                        data_min_date,
                        data_max_date,
                    )

            p_col1, p_col2, p_col3, p_col4 = st.columns([1, 1, 1, 2])
            with p_col1:
                st.button(
                    "Last 7 days",
                    width="stretch",
                    on_click=_set_date_range,
                    args=(preset_7_start, today_date),
                )
            with p_col2:
                st.button(
                    "Last 30 days",
                    width="stretch",
                    on_click=_set_date_range,
                    args=(preset_30_start, today_date),
                )
            with p_col3:
                st.button(
                    "Month to date",
                    width="stretch",
                    on_click=_set_date_range,
                    args=(preset_mtd_start, today_date),
                )
            with p_col4:
                selected_range = st.date_input(
                    "Scan date range",
                    key="audit_date_range",
                    min_value=min(data_min_date, preset_30_start, preset_mtd_start),
                    max_value=max(data_max_date, today_date),
                    label_visibility="collapsed",
                )

            # A range picker reports a 1-tuple while the user is mid-selection.
            if isinstance(selected_range, (tuple, list)):
                if len(selected_range) == 2:
                    range_start, range_end = selected_range
                elif len(selected_range) == 1:
                    range_start = range_end = selected_range[0]
                else:
                    range_start, range_end = data_min_date, data_max_date
            elif selected_range is not None:
                range_start = range_end = selected_range
            else:
                range_start, range_end = data_min_date, data_max_date

            # -------------------------------------------------
            # Filter controls: row 2 dropdowns
            # -------------------------------------------------
            f_col1, f_col2, f_col3 = st.columns(3)
            with f_col1:
                invoice_options = ["All Invoices"] + sorted(
                    audit_df["invoice_number"].dropna().unique().tolist()
                )
                selected_invoice = st.selectbox("Carrier invoice ID", invoice_options)
            with f_col2:
                urgency_options = [
                    "All Claims",
                    URGENCY_CRITICAL,
                    URGENCY_ACTIVE,
                    URGENCY_SENT,
                ]
                selected_urgency = st.selectbox("Urgency / aging", urgency_options)
            with f_col3:
                channel_options = ["All Channels"] + sorted(
                    audit_df["source_channel"].dropna().unique().tolist()
                )
                selected_channel = st.selectbox("Ingestion source", channel_options)

            # -------------------------------------------------
            # Apply every active criterion at once
            # -------------------------------------------------
            filter_mask = (audit_df["scan_date"] >= pd.Timestamp(range_start)) & (
                audit_df["scan_date"] <= pd.Timestamp(range_end)
            )
            if selected_invoice != "All Invoices":
                filter_mask &= audit_df["invoice_number"] == selected_invoice
            if selected_urgency != "All Claims":
                filter_mask &= audit_df["aging_urgency"] == selected_urgency
            if selected_channel != "All Channels":
                filter_mask &= audit_df["source_channel"] == selected_channel

            # Explicit constructor avoids pandas type-stub ambiguity for mask selection.
            filtered_df = pd.DataFrame(audit_df[filter_mask], copy=True)

            st.caption(
                f"Showing {len(filtered_df):,} of {len(audit_df):,} audited shipments "
                f"· scans on file span {data_min_date} to {data_max_date}"
            )

            if filtered_df.empty:
                st.info(
                    "No shipments match the current filters. The loaded mart covers "
                    f"**{data_min_date} to {data_max_date}**, so a recent-date preset "
                    "can legitimately return nothing."
                )
                st.button(
                    "Reset to full data range",
                    on_click=_set_date_range,
                    args=(data_min_date, data_max_date),
                )
            else:
                # -------------------------------------------------
                # Bulk operations
                # -------------------------------------------------
                eligible_unsent = pd.DataFrame(
                    filtered_df[
                        (filtered_df["is_eligible_for_dispute"])
                        & (filtered_df["dispute_status"] != "DISPUTE_SENT")
                    ],
                    copy=True,
                )
                high_confidence = pd.DataFrame(
                    eligible_unsent[
                        eligible_unsent["dollar_variance"] >= HIGH_CONFIDENCE_CLAIM_USD
                    ],
                    copy=True,
                )

                b_col1, b_col2 = st.columns(2)
                with b_col1:
                    portal_columns = {
                        "invoice_number": "Carrier_Invoice_Number",
                        "tracking_id": "Disputed_Tracking_ID",
                        "dollar_variance": "Claim_Refund_Amount",
                        "anomaly_classification": "Dispute_Reason_Code",
                    }
                    portal_batch = eligible_unsent.rename(columns=portal_columns)[
                        [
                            "Carrier_Invoice_Number",
                            "Disputed_Tracking_ID",
                            "carrier_name",
                            "billed_total",
                            "expected_total_cost",
                            "Claim_Refund_Amount",
                            "weight_variance_lbs",
                            "Dispute_Reason_Code",
                        ]
                    ]
                    st.download_button(
                        f"⬇️ Carrier portal batch CSV ({len(portal_batch):,} claims)",
                        data=portal_batch.to_csv(index=False),
                        file_name=f"dispute_batch_UPS_{today_date:%Y%m%d}.csv",
                        mime="text/csv",
                        type="primary",
                        width="stretch",
                        disabled=portal_batch.empty,
                        help="Formatted for carrier portal batch upload.",
                    )
                with b_col2:
                    if st.button(
                        f"⚡ Batch approve {len(high_confidence):,} high-confidence claims",
                        width="stretch",
                        disabled=high_confidence.empty,
                        help=(
                            "Files every filtered claim worth "
                            f"${HIGH_CONFIDENCE_CLAIM_USD:,.2f} or more."
                        ),
                    ):
                        claimed, failures = 0.0, []
                        for claim in high_confidence.to_dict("records"):
                            try:
                                update_dispute_status_in_db(claim)
                                claimed += float(claim["dollar_variance"])
                            except Exception as exc:
                                failures.append(f"{claim['tracking_id']}: {exc}")

                        submitted = len(high_confidence) - len(failures)
                        if submitted:
                            st.toast(f"Filed {submitted} dispute claims.", icon="⚖️")
                            st.session_state["audit_flash"] = (
                                f"🎉 Batch approved {submitted} high-confidence claims "
                                f"totalling ${claimed:,.2f}."
                            )
                        if failures:
                            st.session_state["audit_flash_error"] = (
                                "Some claims could not be filed:\n\n- "
                                + "\n- ".join(failures)
                            )
                        # Rerun either way so the grid reflects the rows that did land.
                        st.rerun()

                # -------------------------------------------------
                # Filtered data grid
                # -------------------------------------------------
                display_cols = [
                    "tracking_id",
                    "invoice_number",
                    "source_channel",
                    "carrier_name",
                    "actual_scale_weight",
                    "billed_weight",
                    "weight_variance_lbs",
                    "expected_total_cost",
                    "billed_total",
                    "dollar_variance",
                    "anomaly_classification",
                    "aging_urgency",
                    "dispute_status",
                ]
                usd = st.column_config.NumberColumn(format="$%.2f")
                lbs = st.column_config.NumberColumn(format="%.2f")
                st.dataframe(
                    filtered_df[display_cols],
                    width="stretch",
                    height=300,
                    hide_index=True,
                    column_config={
                        "actual_scale_weight": lbs,
                        "billed_weight": lbs,
                        "weight_variance_lbs": lbs,
                        "expected_total_cost": usd,
                        "billed_total": usd,
                        "dollar_variance": usd,
                    },
                )

                st.divider()

                # -------------------------------------------------
                # Single-item deep dive & AI dispute resolution
                # -------------------------------------------------
                st.markdown("### ⚖️ Human-in-the-Loop (HITL) Dispute Resolution")
                st.caption(
                    "Select a flagged anomaly to review audit evidence, generate an AI claim letter, and submit for carrier credit recovery."
                )

                # Deep dive follows the filters, so reviewers only see claims from
                # the slice they are working. Explicit constructor avoids pandas
                # type-stub ambiguity for boolean mask selection.
                disputable_df = pd.DataFrame(
                    filtered_df[filtered_df["is_eligible_for_dispute"]], copy=True
                )

                if disputable_df.empty:
                    st.info(
                        "No shipments in the current selection are flagged for dispute."
                    )
                else:
                    # Keyed by tracking id so labelling the dropdown and resolving the
                    # selection don't re-scan the frame once per option.
                    disputable_rows = {
                        str(row["tracking_id"]): row
                        for row in disputable_df.to_dict("records")
                    }
                    selected_trk = st.selectbox(
                        "Select flagged tracking number for review",
                        options=list(disputable_rows),
                        format_func=lambda x: (
                            f"{x} (Overcharge: +${disputable_rows[x]['dollar_variance']:.2f}"
                            f" | {disputable_rows[x]['anomaly_classification']})"
                        ),
                    )

                    selected_row = disputable_rows[str(selected_trk)]
                    days_left = int(selected_row["days_remaining"])

                    # Audit Evidence Comparison Card
                    st.markdown("#### 🔍 Audit Evidence Ground Truth")
                    e_col1, e_col2, e_col3 = st.columns(3)
                    with e_col1:
                        st.markdown("**Physical Warehouse Reality (WMS):**")
                        st.write(
                            f"- Scale Weight: **{selected_row['actual_scale_weight']} lbs**"
                        )
                        st.write(
                            f"- Scan Timestamp: `{selected_row['scan_timestamp']}`"
                        )
                        st.write(
                            f"- Expected Cost: **${selected_row['expected_total_cost']:.2f}**"
                        )
                    with e_col2:
                        st.markdown("**Carrier Invoiced Reality:**")
                        st.write(
                            f"- Billed Weight: **{selected_row['billed_weight']} lbs**"
                        )
                        st.write(f"- Source Feed: `{selected_row['source_channel']}`")
                        st.write(
                            f"- Billed Amount: **${selected_row['billed_total']:.2f}**"
                        )
                    with e_col3:
                        st.markdown("**Audit Findings & Filing Window:**")
                        st.write(
                            f"- Weight Delta: **+{selected_row['weight_variance_lbs']} lbs**"
                        )
                        st.write(
                            f"- Net Overcharge: **+${selected_row['dollar_variance']:.2f}**"
                        )
                        st.write(
                            f"- Current Status: `{selected_row['dispute_status']}`"
                        )
                        if selected_row["dispute_status"] == "DISPUTE_SENT":
                            st.write("- Filing Window: **Claim already filed**")
                        elif days_left < 0:
                            st.write(
                                f"- Filing Window: **Expired {abs(days_left)} days ago**"
                            )
                        else:
                            st.write(f"- Filing Window: **{days_left} days remaining**")

                    if (
                        selected_row["dispute_status"] != "DISPUTE_SENT"
                        and days_left < 0
                    ):
                        st.warning(
                            f"⏳ This claim is {abs(days_left)} days past the "
                            f"{DISPUTE_WINDOW_DAYS}-day contractual filing window and the "
                            "carrier may reject it."
                        )

                    # Action: Generate AI Dispute Letter
                    st.write("")
                    if st.button(
                        "📝 Generate Individual Formal Dispute Letter", type="primary"
                    ):
                        with st.spinner(
                            "Calling AI Dispute Agent to compile evidence-backed claim letter..."
                        ):
                            dispute_payload = {
                                "invoice_number": str(selected_row["invoice_number"]),
                                "carrier_name": str(selected_row["carrier_name"]),
                                "tracking_id": str(selected_row["tracking_id"]),
                                "billed_weight": float(selected_row["billed_weight"]),
                                "actual_weight": float(
                                    selected_row["actual_scale_weight"]
                                ),
                                "billed_cost": float(selected_row["billed_total"]),
                                "expected_cost": float(
                                    selected_row["expected_total_cost"]
                                ),
                                "dollar_variance": float(
                                    selected_row["dollar_variance"]
                                ),
                            }

                            dispute_letter_text = ""
                            try:
                                # 1. Attempt calling the live FastAPI REST endpoint
                                resp = requests.post(
                                    f"{API_BASE_URL}/api/generate-dispute",
                                    json=dispute_payload,
                                    timeout=10.0,
                                )
                                if resp.status_code == 200:
                                    dispute_letter_text = resp.json().get(
                                        "dispute_letter", ""
                                    )
                                else:
                                    raise RuntimeError(
                                        f"API returned status {resp.status_code}"
                                    )
                            except Exception:
                                # 2. Resilient In-Process Fallback if FastAPI server is not currently running
                                req_obj = DisputeRequest(**dispute_payload)
                                agent_res = dispute_agent.generate_letter(req_obj)
                                dispute_letter_text = agent_res.dispute_letter

                            st.session_state["active_dispute_letter"] = (
                                dispute_letter_text
                            )
                            st.session_state["active_dispute_tracking"] = selected_trk

                    # Render Claim Letter and Final Approval Button
                    if st.session_state.get("active_dispute_tracking") == selected_trk:
                        st.markdown("#### 📄 Executive Dispute Letter Draft")
                        edited_letter = (
                            st.text_area(
                                label="Review and edit claim letter prior to submission:",
                                value=st.session_state.get("active_dispute_letter", ""),
                                height=220,
                            )
                            or ""
                        )
                        # Keep reviewer edits across the reruns triggered by the buttons below.
                        st.session_state["active_dispute_letter"] = edited_letter

                        col_sub1, col_sub2 = st.columns([1, 2])
                        with col_sub1:
                            if st.button(
                                "⚖️ Approve & Submit Single Dispute",
                                type="primary",
                                width="stretch",
                            ):
                                try:
                                    update_dispute_status_in_db(selected_row)
                                    st.session_state["audit_flash"] = (
                                        f"🎉 Dispute for {selected_trk} approved and submitted "
                                        f"for ${float(selected_row['dollar_variance']):,.2f}."
                                    )
                                    st.toast(
                                        "Database record updated to DISPUTE_SENT.",
                                        icon="⚖️",
                                    )
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to update database: {e}")
                        with col_sub2:
                            st.download_button(
                                "⬇️ Download Claim Letter",
                                data=edited_letter,
                                file_name=f"dispute_{selected_trk}.txt",
                                mime="text/plain",
                                width="stretch",
                            )

    except Exception as e:
        st.error(f"Failed to load reconciliation mart: {e}")
        st.info(
            "💡 Ensure PostgreSQL is running on port 5432 and you have executed `dbt run` to build `analytics_marts.fct_reconciliation_marts`."
        )
