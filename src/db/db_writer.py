import os
import psycopg2
from dotenv import load_dotenv
from typing import List
from src.agent.schemas import CarrierInvoiceExtraction, InvoiceItem

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://postgres:postgres@localhost:5432/logistics_db"
)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def insert_extracted_invoice(invoice: CarrierInvoiceExtraction, source_label: str = "PORTAL_PDF_AI") -> int:
    """
    Inserts a validated Pydantic CarrierInvoiceExtraction document into 
    source_enterprise.raw_portal_extracted_invoices in PostgreSQL.
    
    Returns:
        int: Number of line items successfully inserted.
    """
    insert_query = """
    INSERT INTO source_enterprise.raw_portal_extracted_invoices (
        tracking_id,
        invoice_number,
        carrier_name,
        billed_weight,
        base_charge,
        fuel_surcharge,
        grand_total,
        extraction_source
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
    """

    records_to_insert = []
    for item in invoice.items:
        records_to_insert.append((
            item.tracking_id,
            invoice.invoice_number,
            invoice.carrier_name,
            item.billed_weight,
            item.base_charge,
            item.fuel_surcharge,
            item.grand_total,
            source_label
        ))

    if not records_to_insert:
        return 0

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_query, records_to_insert)
            conn.commit()

    return len(records_to_insert)


if __name__ == "__main__":
    # Test execution with a sample Pydantic extraction
    sample_item = InvoiceItem(
        tracking_id="1Z9990000000009999",
        billed_weight=15.0,
        base_charge=35.00,
        fuel_surcharge=5.00,
        tax=0.0,
        grand_total=40.00
    )
    
    sample_invoice = CarrierInvoiceExtraction(
        invoice_number="TEST-MANUAL-001",
        carrier_name="UPS",
        invoice_date="2026-02-12",
        items=[sample_item]
    )

    rows_inserted = insert_extracted_invoice(sample_invoice)
    print(f"✅ DB Writer Test: Successfully inserted {rows_inserted} line item into PostgreSQL.")