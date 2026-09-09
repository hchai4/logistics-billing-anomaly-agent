import sys
from pathlib import Path

import streamlit as st
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent.llm_agent import InvoiceExtractionAgent

# ---------------------------------------------------------
# Page Configuration & Styling
# ---------------------------------------------------------
st.set_page_config(
    page_title="AI Logistics Billing Audit & Anomaly Agent",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---------------------------------------------------------
# Sample Test Invoices (Pre-loaded for Quick Demos)
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
# Helper Functions & Agent Initialization
# ---------------------------------------------------------
@st.cache_resource
def get_extraction_agent():
    """Initializes the agent once and caches it across user interactions."""
    return InvoiceExtractionAgent()


agent = get_extraction_agent()

# ---------------------------------------------------------
# Sidebar: Controls & Sample Data Loader
# ---------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Audit Controls")
    st.markdown("Load pre-configured test cases to evaluate the multi-layer defense engine:")

    if st.button("Load Valid Invoice (Math Passes)", use_container_width=True):
        st.session_state["invoice_input"] = SAMPLE_PASSING_INVOICE

    if st.button("Load Corrupted Invoice (Math Fails)", use_container_width=True):
        st.session_state["invoice_input"] = SAMPLE_FAILING_INVOICE

    st.divider()
    st.markdown("**Active Defenses:**")
    st.markdown("- **Privacy Layer:** PII Masking (SpaCy + Regex)")
    st.markdown("- **Defense 1:** Schema Guardrails (Pydantic)")
    st.markdown("- **Defense 2:** Math Verification (Python Engine)")

# ---------------------------------------------------------
# Main Panel: Header & Inputs
# ---------------------------------------------------------
st.title("📦 Logistics Billing Anomaly & AI Audit Agent")
st.caption("Automated carrier invoice extraction, privacy masking, and deterministic arithmetic verification.")

# Default session state initialization
if "invoice_input" not in st.session_state:
    st.session_state["invoice_input"] = SAMPLE_PASSING_INVOICE

# User input text area
invoice_text_input = st.text_area(
    label="Carrier Invoice Raw Text / Snippet:",
    value=st.session_state["invoice_input"],
    height=200,
    help="Paste raw text extracted from a PDF invoice or carrier email."
)
invoice_text = invoice_text_input or ""

# Execution Button
run_audit = st.button("🚀 Run AI Audit & Verification", type="primary", use_container_width=True)

# ---------------------------------------------------------
# Execution & Results Rendering
# ---------------------------------------------------------
if run_audit:
    if not invoice_text.strip():
        st.warning("⚠️ Please provide invoice text to analyze.")
    else:
        with st.spinner("Scrubbing PII, extracting structured schema, and running arithmetic verification..."):
            try:
                extracted_data, pii_stats, math_report = agent.extract(invoice_text)

                st.divider()

                # --- 1. Top Status Badge (Defense 2 Result) ---
                if math_report.is_passed:
                    st.success(
                        "### ✅ DEFENSE 2 PASSED: 100% Arithmetic Integrity Verified\n"
                        "All extracted line items match calculated base charges, fuel surcharges, and taxes exactly."
                    )
                else:
                    st.error(
                        f"### ❌ DEFENSE 2 FAILED: {math_report.failed_items_count} Calculation Discrepancy Detected\n"
                        "One or more line items failed deterministic arithmetic verification. Ground-truth math does not match billed totals."
                    )

                # --- 2. Executive Metric Cards ---
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric(label="Invoice Number", value=extracted_data.invoice_number)
                with col2:
                    st.metric(label="Carrier", value=extracted_data.carrier_name)
                with col3:
                    st.metric(
                        label="Math Status",
                        value="PASSED" if math_report.is_passed else "FAILED",
                        delta="Valid" if math_report.is_passed else f"-{math_report.failed_items_count} Discrepancies",
                        delta_color="normal" if math_report.is_passed else "inverse"
                    )
                with col4:
                    total_redacted = sum(pii_stats.values())
                    st.metric(label="PII Entities Scrubbed", value=f"{total_redacted} fields")

                st.write("")

                # --- 3. Detailed Results Tabs ---
                tab_table, tab_json, tab_privacy = st.tabs([
                    "📊 Line Item Verification Table",
                    "🔍 Extracted Pydantic JSON",
                    "🛡️ Privacy Audit & Masking"
                ])

                # Tab 1: Tabular View with Status Badges
                with tab_table:
                    st.subheader("Line Item Breakdown & Variance Analysis")
                    table_rows = []
                    for item, math_res in zip(extracted_data.items, math_report.item_results):
                        table_rows.append({
                            "Tracking ID": item.tracking_id,
                            "Billed Weight (lbs)": item.billed_weight,
                            "Base Rate ($)": f"${item.base_charge:.2f}",
                            "Fuel Surcharge ($)": f"${item.fuel_surcharge:.2f}",
                            "Tax ($)": f"${item.tax:.2f}",
                            "Billed Total ($)": f"${item.grand_total:.2f}",
                            "Calculated Total ($)": f"${math_res.calculated_total:.2f}",
                            "Math Variance ($)": f"${math_res.variance:.2f}",
                            "Status": "✅ PASSED" if math_res.is_valid else "❌ FAILED"
                        })
                    
                    df_items = pd.DataFrame(table_rows)
                    st.dataframe(df_items, use_container_width=True)

                # Tab 2: Raw Structured JSON View
                with tab_json:
                    st.subheader("Type-Safe Structured Output (Pydantic Model)")
                    st.json(extracted_data.model_dump())

                # Tab 3: Privacy & Security Metrics View
                with tab_privacy:
                    st.subheader("Ethical AI / Privacy Shield Report")
                    st.markdown("Entity counts scrubbed before sending text to external LLM endpoints:")
                    st.write(pii_stats)
                    st.info("💡 **Compliance Note:** Customer names, phone numbers, emails, and street addresses are stripped at the local boundary using SpaCy NER and Regex.")

            except Exception as e:
                st.error(f"❌ An error occurred during the audit pipeline: {str(e)}")