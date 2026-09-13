import os
import random
from datetime import datetime, timedelta
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://postgres:postgres@localhost:5432/logistics_db"
)


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def initialize_schemas_and_tables():
    """
    Creates enterprise schemas and raw ingestion tables in PostgreSQL.
    """
    ddl_statements = """
    -- 1. Create Enterprise Schemas
    CREATE SCHEMA IF NOT EXISTS source_enterprise;
    CREATE SCHEMA IF NOT EXISTS analytics_marts;

    -- 2. Source 1: Internal WMS Shipping History (Physical Ground Truth)
    DROP TABLE IF EXISTS source_enterprise.raw_wms_package_scans CASCADE;
    CREATE TABLE source_enterprise.raw_wms_package_scans (
        package_id VARCHAR(64) PRIMARY KEY,
        tracking_id VARCHAR(64) NOT NULL UNIQUE,
        actual_scale_weight NUMERIC(8, 2) NOT NULL,
        scan_timestamp TIMESTAMP NOT NULL,
        origin_warehouse VARCHAR(32) NOT NULL,
        expected_cost NUMERIC(10, 2) NOT NULL
    );

    -- 3. Source 2: UPS EDI 210 Invoice Feed (Structured Corporate Ingestion)
    DROP TABLE IF EXISTS source_enterprise.raw_edi_ups_invoices CASCADE;
    CREATE TABLE source_enterprise.raw_edi_ups_invoices (
        edi_record_id VARCHAR(64) PRIMARY KEY,
        tracking_id VARCHAR(64) NOT NULL,
        carrier_invoice_id VARCHAR(64) NOT NULL,
        billed_weight NUMERIC(8, 2) NOT NULL,
        base_rate NUMERIC(10, 2) NOT NULL,
        fuel_surcharge NUMERIC(10, 2) NOT NULL,
        total_billed_amount NUMERIC(10, 2) NOT NULL,
        invoice_date DATE NOT NULL
    );

    -- 4. Source 3: Portal PDF & Email Extractions (AI Agent Landing Table)
    DROP TABLE IF EXISTS source_enterprise.raw_portal_extracted_invoices CASCADE;
    CREATE TABLE source_enterprise.raw_portal_extracted_invoices (
        portal_record_id SERIAL PRIMARY KEY,
        tracking_id VARCHAR(64) NOT NULL,
        invoice_number VARCHAR(64) NOT NULL,
        carrier_name VARCHAR(32) NOT NULL,
        billed_weight NUMERIC(8, 2) NOT NULL,
        base_charge NUMERIC(10, 2) NOT NULL,
        fuel_surcharge NUMERIC(10, 2) NOT NULL,
        grand_total NUMERIC(10, 2) NOT NULL,
        extraction_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        extraction_source VARCHAR(32) DEFAULT 'PORTAL_PDF_AI'
    );
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl_statements)
            conn.commit()
    print("✅ Schemas and raw tables created successfully in PostgreSQL.")


def seed_synthetic_data(num_records: int = 50):
    """
    Populates source_enterprise with relational test data sharing matching tracking_ids.
    Includes:
    - Normal matching shipments
    - Weight inflation overcharge anomalies (WMS actual: 4.2 lbs vs Billed: 18.5 lbs)
    - Surcharge discrepancies
    """
    base_time = datetime(2026, 2, 1, 8, 0, 0)
    warehouses = ["ONT8_CALIFORNIA", "LAX9_CALIFORNIA", "PHX6_ARIZONA", "DFW7_TEXAS"]

    wms_rows = []
    edi_rows = []
    portal_rows = []

    for i in range(1, num_records + 1):
        # Generate clean, standard tracking ID (e.g., 1Z9990000000000001)
        tracking_id = f"1Z999000000000{i:04d}"
        package_id = f"PKG-2026-{i:05d}"
        warehouse = random.choice(warehouses)
        scan_time = base_time + timedelta(hours=i, minutes=random.randint(5, 55))
        invoice_date = scan_time.date() + timedelta(days=2)

        # Baseline physical weight recorded by WMS scale
        actual_weight = round(random.uniform(1.5, 8.0), 2)
        expected_cost = round(12.00 + (actual_weight * 1.50), 2)

        wms_rows.append((
            package_id, tracking_id, actual_weight, scan_time, warehouse, expected_cost
        ))

        # Determine Scenario based on package index:
        if i == 1:
            # Anomaly 1: The flagship Newegg $100K case (Huge weight inflation in Portal PDF)
            portal_rows.append((
                tracking_id, "UPS-994112", "UPS",
                18.50,   # Billed: 18.5 lbs vs WMS: ~4.2 lbs!
                45.00, 8.50, 53.50,
                scan_time + timedelta(days=3)
            ))
        elif i == 2:
            # Anomaly 2: EDI Overcharge Anomaly (UPS EDI billed extra surcharge)
            edi_rows.append((
                f"EDI-REC-{i:05d}", tracking_id, "UPS-EDI-883900",
                actual_weight,
                expected_cost,
                15.50,  # Unwarranted high surcharge
                round(expected_cost + 15.50, 2),
                invoice_date
            ))
        elif i % 3 == 0:
            # Standard Portal PDF extractions (Matches WMS)
            portal_rows.append((
                tracking_id, f"INV-PORTAL-{i:04d}", "UPS",
                actual_weight,
                round(expected_cost * 0.85, 2),
                round(expected_cost * 0.15, 2),
                expected_cost,
                scan_time + timedelta(days=2)
            ))
        else:
            # Standard EDI 210 feed records (Matches WMS within small variance)
            billed_weight = round(actual_weight + random.uniform(0.0, 0.2), 2)
            base_rate = round(expected_cost * 0.85, 2)
            fuel = round(expected_cost * 0.15, 2)
            total_billed = round(base_rate + fuel, 2)

            edi_rows.append((
                f"EDI-REC-{i:05d}", tracking_id, f"EDI-INV-{i:04d}",
                billed_weight, base_rate, fuel, total_billed, invoice_date
            ))

    # Insert batch data into PostgreSQL
    insert_wms_query = """
    INSERT INTO source_enterprise.raw_wms_package_scans
    (package_id, tracking_id, actual_scale_weight, scan_timestamp, origin_warehouse, expected_cost)
    VALUES (%s, %s, %s, %s, %s, %s);
    """

    insert_edi_query = """
    INSERT INTO source_enterprise.raw_edi_ups_invoices
    (edi_record_id, tracking_id, carrier_invoice_id, billed_weight, base_rate, fuel_surcharge, total_billed_amount, invoice_date)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
    """

    insert_portal_query = """
    INSERT INTO source_enterprise.raw_portal_extracted_invoices
    (tracking_id, invoice_number, carrier_name, billed_weight, base_charge, fuel_surcharge, grand_total, extraction_timestamp)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_wms_query, wms_rows)
            cur.executemany(insert_edi_query, edi_rows)
            cur.executemany(insert_portal_query, portal_rows)
            conn.commit()

    print(f"✅ Seeding Complete:")
    print(f"   - WMS Scans: {len(wms_rows)} records")
    print(f"   - UPS EDI Records: {len(edi_rows)} records")
    print(f"   - Portal PDF Records: {len(portal_rows)} records")


if __name__ == "__main__":
    print("=== INITIALIZING POSTGRESQL MULTI-SOURCE DATABASE ===")
    initialize_schemas_and_tables()
    seed_synthetic_data(num_records=50)