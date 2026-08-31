import os
import instructor
from openai import OpenAI
from dotenv import load_dotenv
from typing import Tuple, Dict, Any

from src.privacy.pii_masker import PIIMasker
from src.agent.schemas import CarrierInvoiceExtraction
from src.agent.math_verifier import MathVerifier, MathVerificationReport

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
if not api_key or api_key == "your_actual_openai_api_key_here":
    raise ValueError("Please set a valid OPENAI_API_KEY in your .env file.")

client = instructor.from_openai(OpenAI(api_key=api_key))


class InvoiceExtractionAgent:
    """
    Production Agent incorporating Privacy Guardrails, Schema Enforcement (Defense 1),
    and Deterministic Math Verification (Defense 2).
    """

    def __init__(self, model_name: str = "gpt-4o-mini"):
        self.model_name = model_name
        self.masker = PIIMasker()
        self.math_verifier = MathVerifier()

    def extract(self, raw_invoice_text: str) -> Tuple[CarrierInvoiceExtraction, Dict[str, Any], MathVerificationReport]:
        """
        Runs full end-to-end extraction pipeline:
        1. Privacy Layer: Redacts PII.
        2. Defense 1: Instructor + Pydantic schema enforcement with auto-retry.
        3. Defense 2: Deterministic Python math verification checks.
        """
        # Step 1: Privacy Masking
        masked_text, pii_stats = self.masker.mask(raw_invoice_text)

        # Step 2: Instructor Extraction (Defense 1)
        system_prompt = (
            "You are an expert enterprise logistics data extraction agent. "
            "Your job is to parse unstructured carrier invoices and extract "
            "structured billing details into the exact schema requested."
        )

        extracted_data: CarrierInvoiceExtraction = client.chat.completions.create(
            model=self.model_name,
            response_model=CarrierInvoiceExtraction,
            max_retries=3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract invoice data from this text:\n\n{masked_text}"},
            ],
            temperature=0.0
        )

        # Step 3: Math Verification (Defense 2)
        math_report = self.math_verifier.verify_invoice(extracted_data)

        return extracted_data, pii_stats, math_report


if __name__ == "__main__":
    sample_raw_invoice = """
    UPS FREIGHT INVOICE #UPS-994112
    Date: February 10, 2026
    Billing Contact: Customer Sarah Smith (email: sarah@ecomm-store.com)
    Shipping Address: 123 Industrial Pkwy, Los Angeles, CA 90001
    
    Line Item 1:
    Tracking Number: 1z 999 999 9999 9999 99
    Billed Weight: 18.5 lbs
    Base Rate: $45.00
    Fuel Surcharge: $8.50
    Total Item Charge: $53.50
    """

    agent = InvoiceExtractionAgent()
    data, pii_stats, math_report = agent.extract(sample_raw_invoice)

    print("=== END-TO-END AGENT TEST ===")
    print(f"PII Redacted: {pii_stats}")
    print(f"Math Check Passed: {math_report.is_passed}")
    print(f"Invoice Total Line Items: {len(data.items)}")