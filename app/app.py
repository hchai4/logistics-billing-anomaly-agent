import os
import sys
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
    "DATABASE_URL", 
    "postgresql://postgres:postgres@localhost:5432/logistics_db"
)
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

# ---------------------------------------------------------
# Page Configuration
# ---------------------------------------------------------
st.set_page_config(
    page_title="Enterprise Logistics Audit & AI Dispute Hub",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
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
    "tracking_id", "package_id", "invoice_number", "carrier_name", "source_channel",
    "actual_scale_weight", "billed_weight", "weight_variance_lbs",
    "expected_total_cost", "billed_total", "dollar_variance", "anomaly_classification",
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
    update_sql = text("""
    UPDATE source_enterprise.audit_flagged_exceptions
    SET dispute_status = 'DISPUTE_SENT', flagged_at = CURRENT_TIMESTAMP
    WHERE tracking_id = :tracking_id;
    """)
    insert_sql = text("""
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
    """)
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
st.caption("Integrated Ingestion, dbt Source-to-Source Reconciliation, and Human-in-the-Loop Claims Recovery")

tab_ingestion, tab_reconciliation = st.tabs([
    "📥 Tab 1: Live Invoice Ingestion & Defense Gate",
    "📊 Tab 2: Enterprise 3-Way Audit Mart & Disputes"
])


# =========================================================
# TAB 1: LIVE INVOICE INGESTION & DEFENSE GATE
# =========================================================
with tab_ingestion:
    st.subheader("Ingest Unstructured Carrier Invoices (Portal PDF / Email)")
    st.markdown("Upload carrier bills to sanitize customer PII, enforce Pydantic schemas, and run math checks before warehouse ingestion.")

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
        help="Upload an invoice to trigger ingress PII redaction."
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
            st.success(f"📄 Processed '{uploaded_file.name}'! (Scrubbed {sum(pii_stats.values())} PII fields at UI boundary)")
        except Exception as e:
            st.error(f"Failed to read file: {e}")

    invoice_text = st.text_area(
        label="Active Invoice Content (Pre-processed & Sanitized):",
        value=st.session_state["invoice_input"],
        height=180
    )

    if st.button("🚀 Run AI Extraction & Math Verification", type="primary", use_container_width=True):
        if invoice_text is None or not invoice_text.strip():
            st.warning("⚠️ Please provide invoice text to analyze.")
        else:
            with st.spinner("Extracting Pydantic Schema & Running Deterministic Math Verification..."):
                try:
                    extracted_data, pii_stats, math_report = extraction_agent.extract(invoice_text)
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
            st.error(f"### ❌ DEFENSE 2 FAILED: {math_report.failed_items_count} Arithmetic Discrepancy Detected")

        # Metric Cards
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Invoice #", extracted_data.invoice_number)
        c2.metric("Carrier", extracted_data.carrier_name)
        c3.metric(
            "Math Audit", 
            "PASSED" if math_report.is_passed else "FAILED",
            delta="Verified" if math_report.is_passed else f"-{math_report.failed_items_count} Errors",
            delta_color="normal" if math_report.is_passed else "inverse"
        )
        c4.metric("PII Redacted", f"{sum(pii_stats.values())} fields")

        # Detailed Breakdown Table
        table_rows = []
        for item, m_res in zip(extracted_data.items, math_report.item_results):
            table_rows.append({
                "Tracking ID": item.tracking_id,
                "Billed Weight (lbs)": item.billed_weight,
                "Base Rate ($)": f"${item.base_charge:.2f}",
                "Fuel ($)": f"${item.fuel_surcharge:.2f}",
                "Billed Total ($)": f"${item.grand_total:.2f}",
                "Calculated Total ($)": f"${m_res.calculated_total:.2f}",
                "Variance ($)": f"${m_res.variance:.2f}",
                "Status": "✅ PASSED" if m_res.is_valid else "❌ FAILED"
            })
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True)

        # Database Ingestion Action
        st.markdown("#### 💾 Persist Verified Data to Warehouse")
        col_db1, col_db2 = st.columns([2, 1])
        with col_db1:
            st.info("Write these extracted invoice items into `source_enterprise.raw_portal_extracted_invoices` for downstream dbt reconciliation.")
        with col_db2:
            if st.button("📥 Save & Ingest to Database", type="secondary", use_container_width=True):
                try:
                    rows_inserted = insert_extracted_invoice(extracted_data, source_label="PORTAL_PDF_AI")
                    st.success(f"✅ Successfully ingested {rows_inserted} line items into PostgreSQL!")
                    st.toast("Database updated! Run 'dbt run' to refresh analytical marts.", icon="🚀")
                except Exception as e:
                    st.error(f"Database write failed: {e}")


# =========================================================
# TAB 2: ENTERPRISE 3-WAY AUDIT MART & DISPUTES
# =========================================================
with tab_reconciliation:
    st.subheader("3-Way Reconciliation Mart (`fct_reconciliation_marts`)")
    st.markdown("Cross-examining **WMS Physical Ground Truth**, **UPS EDI 210 Billing**, and **AI-Extracted Invoices** via dbt dimensional models.")

    try:
        df_mart = load_reconciliation_mart()

        if df_mart.empty:
            st.warning("⚠️ No reconciliation records found. Please run 'dbt run' in your terminal.")
        else:
            # High-Level Summary Metrics
            total_audited = len(df_mart)
            total_discrepancies = len(df_mart[df_mart["is_eligible_for_dispute"]])
            total_leakage = df_mart[df_mart["dollar_variance"] > 0]["dollar_variance"].sum()
            total_disputes_sent = len(df_mart[df_mart["dispute_status"] == "DISPUTE_SENT"])

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Audited Shipments", f"{total_audited:,}")
            m2.metric("Flagged Overcharges", f"{total_discrepancies:,}", delta=f"{round((total_discrepancies/total_audited)*100, 1)}% Flag Rate", delta_color="inverse")
            m3.metric("Identified Margin Leakage", f"${total_leakage:,.2f}")
            m4.metric("Disputes Submitted", f"{total_disputes_sent:,}", delta=f"${df_mart[df_mart['dispute_status'] == 'DISPUTE_SENT']['dollar_variance'].sum():,.2f} Claimed")

            st.divider()

            # Filters
            f_col1, f_col2, f_col3 = st.columns([1, 1, 1])
            with f_col1:
                channel_options = ["All"] + sorted(list(df_mart["source_channel"].dropna().unique()))
                selected_channel = st.selectbox("Filter Ingestion Channel:", channel_options)

            with f_col2:
                anomaly_options = ["All"] + sorted(list(df_mart["anomaly_classification"].dropna().unique()))
                selected_anomaly = st.selectbox("Filter Anomaly Classification:", anomaly_options)

            with f_col3:
                status_options = ["All", "PENDING_REVIEW", "DISPUTE_SENT"]
                selected_status = st.selectbox("Filter Dispute Status:", status_options)

            # Apply Filters
            filtered_df = df_mart.copy()
            if selected_channel != "All":
                filtered_df = filtered_df[filtered_df["source_channel"] == selected_channel]
            if selected_anomaly != "All":
                filtered_df = filtered_df[filtered_df["anomaly_classification"] == selected_anomaly]
            if selected_status != "All":
                filtered_df = filtered_df[filtered_df["dispute_status"] == selected_status]

            # Display Data Table
            display_cols = [
                "tracking_id", "source_channel", "carrier_name", "invoice_number",
                "actual_scale_weight", "billed_weight", "weight_variance_lbs",
                "expected_total_cost", "billed_total", "dollar_variance",
                "anomaly_classification", "dispute_status"
            ]
            st.dataframe(filtered_df[display_cols], use_container_width=True, height=280)

            st.divider()

            # --- Human-in-the-Loop Dispute Generation Section ---
            st.markdown("### ⚖️ Human-in-the-Loop (HITL) Dispute Resolution")
            st.caption("Select a flagged anomaly to review audit evidence, generate an AI claim letter, and submit for carrier credit recovery.")

            # Filter candidates eligible for dispute. The explicit constructor avoids
            # pandas type-stub ambiguity for boolean mask selection.
            disputable_df = pd.DataFrame(df_mart[df_mart["is_eligible_for_dispute"]], copy=True)

            if disputable_df.empty:
                st.info("No shipments currently flagged for dispute.")
            else:
                # Keyed by tracking id so labelling the dropdown and resolving the
                # selection don't re-scan the frame once per option.
                disputable_rows = {
                    str(row["tracking_id"]): row
                    for row in disputable_df.to_dict("records")
                }
                selected_trk = st.selectbox(
                    "Select Flagged Tracking Number for Review:",
                    options=list(disputable_rows),
                    format_func=lambda x: (
                        f"{x} (Overcharge: +${disputable_rows[x]['dollar_variance']:.2f}"
                        f" | {disputable_rows[x]['anomaly_classification']})"
                    )
                )

                selected_row = disputable_rows[str(selected_trk)]

                # Audit Evidence Comparison Card
                st.markdown("#### 🔍 Audit Evidence Ground Truth")
                e_col1, e_col2, e_col3 = st.columns(3)
                with e_col1:
                    st.markdown("**Physical Warehouse Evidence (WMS):**")
                    st.write(f"- Scale Weight: **{selected_row['actual_scale_weight']} lbs**")
                    st.write(f"- Scan Timestamp: `{selected_row['scan_timestamp']}`")
                    st.write(f"- Expected Cost: **${selected_row['expected_total_cost']:.2f}**")
                with e_col2:
                    st.markdown("**Carrier Billed Line (Invoice):**")
                    st.write(f"- Billed Weight: **{selected_row['billed_weight']} lbs**")
                    st.write(f"- Source Feed: `{selected_row['source_channel']}`")
                    st.write(f"- Billed Amount: **${selected_row['billed_total']:.2f}**")
                with e_col3:
                    st.markdown("**Audit Variance Findings:**")
                    st.write(f"- Weight Delta: **+{selected_row['weight_variance_lbs']} lbs**")
                    st.write(f"- Net Overcharge: **+${selected_row['dollar_variance']:.2f}**")
                    st.write(f"- Current Status: `{selected_row['dispute_status']}`")

                # Action: Generate AI Dispute Letter
                st.write("")
                if st.button("📝 Generate Formal Carrier Dispute Letter", type="primary"):
                    with st.spinner("Calling AI Dispute Agent to compile evidence-backed claim letter..."):
                        dispute_payload = {
                            "invoice_number": str(selected_row["invoice_number"]),
                            "carrier_name": str(selected_row["carrier_name"]),
                            "tracking_id": str(selected_row["tracking_id"]),
                            "billed_weight": float(selected_row["billed_weight"]),
                            "actual_weight": float(selected_row["actual_scale_weight"]),
                            "billed_cost": float(selected_row["billed_total"]),
                            "expected_cost": float(selected_row["expected_total_cost"]),
                            "dollar_variance": float(selected_row["dollar_variance"])
                        }

                        dispute_letter_text = ""
                        try:
                            # 1. Attempt calling the live FastAPI REST endpoint
                            resp = requests.post(
                                f"{API_BASE_URL}/api/generate-dispute",
                                json=dispute_payload,
                                timeout=10.0
                            )
                            if resp.status_code == 200:
                                dispute_letter_text = resp.json().get("dispute_letter", "")
                            else:
                                raise RuntimeError(f"API returned status {resp.status_code}")
                        except Exception:
                            # 2. Resilient In-Process Fallback if FastAPI server is not currently running
                            req_obj = DisputeRequest(**dispute_payload)
                            agent_res = dispute_agent.generate_letter(req_obj)
                            dispute_letter_text = agent_res.dispute_letter

                        st.session_state["active_dispute_letter"] = dispute_letter_text
                        st.session_state["active_dispute_tracking"] = selected_trk

                # Render Claim Letter and Final Approval Button
                if st.session_state.get("active_dispute_tracking") == selected_trk:
                    st.markdown("#### 📄 Executive Dispute Letter Draft")
                    edited_letter = st.text_area(
                        label="Review and edit claim letter prior to submission:",
                        value=st.session_state.get("active_dispute_letter", ""),
                        height=220
                    ) or ""
                    # Keep reviewer edits across the reruns triggered by the buttons below.
                    st.session_state["active_dispute_letter"] = edited_letter

                    col_sub1, col_sub2 = st.columns([1, 2])
                    with col_sub1:
                        if st.button("⚖️ Approve & Submit Dispute Claim", type="primary", use_container_width=True):
                            try:
                                update_dispute_status_in_db(selected_row)
                                st.success(f"🎉 Dispute for {selected_trk} successfully approved and submitted!")
                                st.balloons()
                                st.info("Database record updated to `DISPUTE_SENT`. Refresh the table above to view the updated status.")
                            except Exception as e:
                                st.error(f"Failed to update database: {e}")
                    with col_sub2:
                        st.download_button(
                            "⬇️ Download Claim Letter",
                            data=edited_letter,
                            file_name=f"dispute_{selected_trk}.txt",
                            mime="text/plain",
                            use_container_width=True
                        )

    except Exception as e:
        st.error(f"Failed to load reconciliation mart: {e}")
        st.info("💡 Ensure PostgreSQL is running on port 5432 and you have executed `dbt run` to build `analytics_marts.fct_reconciliation_marts`.")
