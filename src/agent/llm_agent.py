import os
import instructor
from openai import OpenAI
from dotenv import load_dotenv
from typing import Tuple, Dict, Any

from src.privacy.pii_masker import PIIMasker
from src.agent.schemas import CarrierInvoiceExtraction

# Load environment variables from .env file
load_dotenv()

# Initialize Instructor-patched OpenAI Client
api_key = os.getenv("OPENAI_API_KEY")
if not api_key or api_key == "your_actual_openai_api_key_here":
    raise ValueError("Please set a valid OPENAI_API_KEY in your .env file.")

# Patch OpenAI client with Instructor for structured extraction
client = instructor.from_openai(OpenAI(api_key=api_key))


class InvoiceExtractionAgent:
    """
    Agent responsible for scrubbing PII and extracting type-safe, validated
    structured JSON from unstructured carrier invoices.
    """

    def __init__(self, model_name: str = "gpt-4o-mini"):
        self.model_name = model_name
        self.masker = PIIMasker()

    def extract(self, raw_invoice_text: str) -> Tuple[CarrierInvoiceExtraction, Dict[str, Any]]:
        """
        Runs the full extraction pipeline:
        1. Privacy Layer: Redacts PII (Emails, Phones, Names, Addresses).
        2. LLM Layer: Instructor + Pydantic schema extraction with auto-retry.
        3. Audit Metrics: Returns PII redaction stats & type-safe object.
        """
        # Step 1: Privacy Masking
        masked_text, pii_stats = self.masker.mask(raw_invoice_text)

        # Step 2: Instructor Structured Extraction (Defense 1: Schema Enforcement)
        system_prompt = (
            "You are an expert enterprise logistics data extraction agent. "
            "Your job is to parse unstructured carrier invoices and extract "
            "structured billing details into the exact schema requested. "
            "If any numeric field is missing, infer 0.0 only if reasonable, "
            "otherwise raise a validation error."
        )

        extracted_data: CarrierInvoiceExtraction = client.chat.completions.create(
            model=self.model_name,
            response_model=CarrierInvoiceExtraction,
            max_retries=3,  # Auto-retry loop if Pydantic validation fails
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract invoice data from this text:\n\n{masked_text}"},
            ],
            temperature=0.0  # Zero temperature for deterministic extraction
        )

        return extracted_data, pii_stats


if __name__ == "__main__":
    # Local Test Execution
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
    
    Line Item 2:
    Tracking Number: 1Z 888 888 8888 8888 88
    Billed Weight: 4.0 lbs
    Base Rate: $12.00
    Fuel Surcharge: $2.00
    Total Item Charge: $14.00
    """

    print("=== STARTING INVOICE EXTRACTION AGENT TEST ===")
    agent = InvoiceExtractionAgent()
    
    try:
        result, pii_metrics = agent.extract(sample_raw_invoice)
        
        print("\n[PRIVACY AUDIT METRICS]")
        print(f"Redacted Entities: {pii_metrics}")

        print("\n[EXTRACTED TYPE-SAFE PYDANTIC OBJECT]")
        print(f"Invoice #: {result.invoice_number}")
        print(f"Carrier: {result.carrier_name}")
        print(f"Date: {result.invoice_date}")
        print(f"Total Line Items: {len(result.items)}")

        print("\n[LINE ITEM DETAILS]")
        for idx, item in enumerate(result.items, 1):
            print(f"  Item {idx}:")
            print(f"    - Cleaned Tracking ID: {item.tracking_id}")
            print(f"    - Billed Weight: {item.billed_weight} lbs (Type: {type(item.billed_weight).__name__})")
            print(f"    - Grand Total: ${item.grand_total} (Type: {type(item.grand_total).__name__})")

        print("\n[RAW JSON DUMP]")
        print(result.model_dump_json(indent=2))

    except Exception as e:
        print(f"\n❌ Extraction Failed: {e}")