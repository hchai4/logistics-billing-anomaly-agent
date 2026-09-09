import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
import pandas as pd
from src.agent.llm_agent import InvoiceExtractionAgent
from src.utils.pdf_extractor import extract_text_from_pdf

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
# Sample Test Invoices (Fallback String Data)
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
    st.markdown("- **Defense 1:** Schema Guardrails (Pydantic + Instructor)")
    st.markdown("- **Defense 2:** Math Verification (Python Engine)")

# ---------------------------------------------------------
# Main Panel: Header & File/Text Input
# ---------------------------------------------------------
st.title("📦 Logistics Billing Anomaly & AI Audit Agent")
st.caption("End-to-End Pipeline: PDF Ingestion ➔ PII Masking ➔ Schema Extraction ➔ Deterministic Math Verification")

if "invoice_input" not in st.session_state:
    st.session_state["invoice_input"] = SAMPLE_PASSING_INVOICE

# 1. File Uploader for Raw PDFs or Text files
uploaded_file = st.file_uploader(
    label="Upload Carrier Invoice (.pdf or .txt):",
    type=["pdf", "txt"],
    help="Upload a real carrier PDF invoice or text file."
)

if uploaded_file is not None:
    try:
        if uploaded_file.name.endswith(".pdf"):
            extracted_pdf_text = extract_text_from_pdf(uploaded_file)
            st.session_state["invoice_input"] = extracted_pdf_text
            st.success(f"📄 Successfully extracted text from '{uploaded_file.name}'!")
        else:
            st.session_state["invoice_input"] = uploaded_file.read().decode("utf-8")
            st.success(f"📄 Successfully loaded text file '{uploaded_file.name}'!")
    except Exception as e:
        st.error(f"Failed to read file: {e}")

# 2. Editable Text Area
invoice_text = st.text_area(
    label="Active Invoice Text (Pre-processed for Audit):",
    value=st.session_state["invoice_input"],
    height=200,
    help="You can edit or verify the text before triggering the AI audit pipeline."
)

# 3. Execution Button
run_audit = st.button("🚀 Run Full AI Audit Pipeline", type="primary", use_container_width=True)

# ---------------------------------------------------------
# Execution & Results Rendering
# ---------------------------------------------------------
if run_audit:
    if not invoice_text.strip():
        st.warning("⚠️ Please provide or upload invoice text to analyze.")
    else:
        with st.spinner("Executing Pipeline: Scrubbing PII ➔ Extracting Schema ➔ Running Math Checks..."):
            try:
                extracted_data, pii_stats, math_report = agent.extract(invoice_text)

                st.divider()

                # --- Status Badge (Defense 2 Verification) ---
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

                # --- Executive Metric Cards ---
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

                # --- Detailed Results Tabs ---
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