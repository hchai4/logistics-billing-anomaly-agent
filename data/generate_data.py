import os
import random
from datetime import datetime, timedelta
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/logistics_db",
)

WAREHOUSE_ORIGIN_ZIPS = {
    "ONT8_CALIFORNIA": "91761",
    "LAX9_CALIFORNIA": "90245",
    "PHX6_ARIZONA": "85043",
    "DFW7_TEXAS": "75063",
}

# Dest ZIP + contracted zone for each origin ZIP3 in seeds/carrier_zone_matrix.csv
ZONE_ROUTES = {
    "750": [("10001", 7), ("60601", 4), ("77001", 3), ("85001", 4)],
    "850": [("10001", 8), ("90001", 3), ("77001", 4), ("80202", 3)],
    "902": [("10001", 8), ("94102", 3), ("77001", 4), ("85001", 4)],
    "917": [("10001", 8), ("94102", 3), ("90001", 3), ("98001", 4)],
}

SERVICE_LEVELS = ["GROUND", "SECOND_DAY_AIR", "NEXT_DAY_AIR"]
TRANSIT_HOURS = {
    "GROUND": 120,
    "SECOND_DAY_AIR": 48,
    "NEXT_DAY_AIR": 24,
}


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def initialize_schemas_and_tables():
    """
    Creates enterprise schemas and raw ingestion tables in PostgreSQL.
    Column sets match the dbt staging models in logistics-billing-anomaly-agent-dbt.
    """
    ddl_statements = """
    CREATE SCHEMA IF NOT EXISTS source_enterprise;
    CREATE SCHEMA IF NOT EXISTS analytics_marts;

    DROP TABLE IF EXISTS source_enterprise.raw_carrier_accessorial_charges CASCADE;
    DROP TABLE IF EXISTS source_enterprise.raw_wms_package_scans CASCADE;
    DROP TABLE IF EXISTS source_enterprise.raw_edi_ups_invoices CASCADE;
    DROP TABLE IF EXISTS source_enterprise.raw_portal_extracted_invoices CASCADE;

    -- Pillar 1: warehouse scale / cubing-laser ground truth
    CREATE TABLE source_enterprise.raw_wms_package_scans (
        package_id VARCHAR(64) PRIMARY KEY,
        tracking_id VARCHAR(64) NOT NULL UNIQUE,
        carrier_code VARCHAR(16) NOT NULL DEFAULT 'UPS',
        service_level VARCHAR(32) NOT NULL,
        actual_scale_weight NUMERIC(8, 2) NOT NULL,
        package_length_in NUMERIC(8, 2) NOT NULL,
        package_width_in NUMERIC(8, 2) NOT NULL,
        package_height_in NUMERIC(8, 2) NOT NULL,
        origin_warehouse VARCHAR(32) NOT NULL,
        origin_zip VARCHAR(16) NOT NULL,
        dest_zip VARCHAR(16) NOT NULL,
        dest_address_type VARCHAR(16) NOT NULL,
        dest_address_cass_validated BOOLEAN NOT NULL DEFAULT TRUE,
        scan_timestamp TIMESTAMP NOT NULL,
        dock_departure_timestamp TIMESTAMP NOT NULL,
        expected_cost NUMERIC(10, 2) NOT NULL
    );

    -- Pillar 3, channel A: EDI 210
    CREATE TABLE source_enterprise.raw_edi_ups_invoices (
        edi_record_id VARCHAR(64) PRIMARY KEY,
        tracking_id VARCHAR(64) NOT NULL,
        carrier_invoice_id VARCHAR(64) NOT NULL,
        billed_weight NUMERIC(8, 2) NOT NULL,
        base_rate NUMERIC(10, 2) NOT NULL,
        fuel_surcharge NUMERIC(10, 2) NOT NULL,
        total_billed_amount NUMERIC(10, 2) NOT NULL,
        service_level_billed VARCHAR(32),
        zone_billed INTEGER,
        dest_zip VARCHAR(16),
        delivery_timestamp TIMESTAMP,
        invoice_date DATE NOT NULL
    );

    -- Pillar 3, channel B: portal / PDF extraction landing table
    CREATE TABLE source_enterprise.raw_portal_extracted_invoices (
        portal_record_id SERIAL PRIMARY KEY,
        tracking_id VARCHAR(64) NOT NULL,
        invoice_number VARCHAR(64) NOT NULL,
        carrier_name VARCHAR(32) NOT NULL,
        billed_weight NUMERIC(8, 2) NOT NULL,
        base_charge NUMERIC(10, 2) NOT NULL,
        fuel_surcharge NUMERIC(10, 2) NOT NULL,
        grand_total NUMERIC(10, 2) NOT NULL,
        service_level_billed VARCHAR(32),
        zone_billed INTEGER,
        dest_zip VARCHAR(16),
        delivery_timestamp TIMESTAMP,
        invoice_date DATE,
        extraction_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        extraction_source VARCHAR(32) DEFAULT 'PORTAL_PDF_AI'
    );

    -- Pillar 3 fee lines
    CREATE TABLE source_enterprise.raw_carrier_accessorial_charges (
        accessorial_charge_id VARCHAR(64) PRIMARY KEY,
        tracking_id VARCHAR(64) NOT NULL,
        invoice_number VARCHAR(64) NOT NULL,
        source_channel VARCHAR(32) NOT NULL,
        charge_line_number INTEGER NOT NULL DEFAULT 1,
        accessorial_code VARCHAR(48) NOT NULL,
        accessorial_amount NUMERIC(10, 2) NOT NULL,
        invoice_date DATE NOT NULL
    );
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl_statements)
            conn.commit()
    print("✅ Schemas and raw tables created successfully in PostgreSQL.")


def _package_dims(index: int) -> tuple[float, float, float]:
    if index == 1:
        return 10.0, 8.0, 6.0
    rng = random.Random(index)
    return (
        round(rng.uniform(8.0, 16.0), 2),
        round(rng.uniform(6.0, 12.0), 2),
        round(rng.uniform(4.0, 10.0), 2),
    )


def seed_synthetic_data(num_records: int = 50):
    """
    Populates source_enterprise with relational test data sharing matching tracking_ids.
    Includes:
    - Normal matching shipments
    - Weight inflation overcharge anomalies (WMS actual: 4.2 lbs vs Billed: 18.5 lbs)
    - Surcharge discrepancies
    - Unauthorized accessorials and a late Next Day Air delivery
    """
    base_time = datetime(2026, 2, 1, 8, 0, 0)
    warehouses = list(WAREHOUSE_ORIGIN_ZIPS.keys())

    wms_rows = []
    edi_rows = []
    portal_rows = []
    accessorial_rows = []

    for i in range(1, num_records + 1):
        tracking_id = f"1Z999000000000{i:04d}"
        package_id = f"PKG-2026-{i:05d}"
        warehouse = warehouses[(i - 1) % len(warehouses)]
        origin_zip = WAREHOUSE_ORIGIN_ZIPS[warehouse]
        dest_zip, zone = ZONE_ROUTES[origin_zip[:3]][(i - 1) % 4]
        scan_time = base_time + timedelta(hours=i, minutes=random.randint(5, 55))
        dock_departure = scan_time + timedelta(hours=2)
        invoice_date = scan_time.date() + timedelta(days=2)

        if i == 1:
            actual_weight = 4.20
            service_level = "GROUND"
            dest_address_type = "COMMERCIAL"
            cass_validated = True
        elif i == 3:
            actual_weight = round(random.uniform(3.0, 7.0), 2)
            service_level = "GROUND"
            dest_address_type = "COMMERCIAL"
            cass_validated = True
        elif i == 5:
            actual_weight = round(random.uniform(2.0, 6.0), 2)
            service_level = "GROUND"
            dest_address_type = "COMMERCIAL"
            cass_validated = True
        elif i == 6:
            actual_weight = round(random.uniform(2.0, 6.0), 2)
            service_level = "GROUND"
            dest_address_type = "RESIDENTIAL"
            cass_validated = True
        elif i == 7:
            actual_weight = round(random.uniform(2.0, 6.0), 2)
            service_level = "GROUND"
            dest_address_type = "COMMERCIAL"
            cass_validated = True
        elif i == 8:
            actual_weight = round(random.uniform(2.0, 5.0), 2)
            service_level = "NEXT_DAY_AIR"
            dest_address_type = "COMMERCIAL"
            cass_validated = True
        else:
            actual_weight = round(random.uniform(1.5, 8.0), 2)
            service_level = SERVICE_LEVELS[i % 3]
            dest_address_type = "RESIDENTIAL" if i % 5 == 0 else "COMMERCIAL"
            cass_validated = i % 11 != 0

        length_in, width_in, height_in = _package_dims(i)
        expected_cost = round(12.00 + (actual_weight * 1.50), 2)
        billed_service = service_level
        billed_zone = zone
        on_time_delivery = dock_departure + timedelta(
            hours=TRANSIT_HOURS[service_level]
        )

        if i == 8:
            delivery_timestamp = dock_departure + timedelta(hours=48)
        else:
            delivery_timestamp = on_time_delivery - timedelta(hours=2)

        wms_rows.append(
            (
                package_id,
                tracking_id,
                "UPS",
                service_level,
                actual_weight,
                length_in,
                width_in,
                height_in,
                warehouse,
                origin_zip,
                dest_zip,
                dest_address_type,
                cass_validated,
                scan_time,
                dock_departure,
                expected_cost,
            )
        )

        if i == 1:
            # Flagship weight-inflation case on the portal channel.
            portal_invoice = "UPS-994112"
            portal_rows.append(
                (
                    tracking_id,
                    portal_invoice,
                    "UPS",
                    18.50,
                    45.00,
                    8.50,
                    53.50,
                    billed_service,
                    billed_zone,
                    dest_zip,
                    delivery_timestamp,
                    invoice_date,
                    scan_time + timedelta(days=3),
                )
            )
        elif i == 2:
            edi_rows.append(
                (
                    f"EDI-REC-{i:05d}",
                    tracking_id,
                    "UPS-EDI-883900",
                    actual_weight,
                    expected_cost,
                    15.50,
                    round(expected_cost + 15.50, 2),
                    billed_service,
                    billed_zone,
                    dest_zip,
                    delivery_timestamp,
                    invoice_date,
                )
            )
        elif i == 3:
            # Service-level upgrade: purchased Ground, billed Next Day Air.
            edi_rows.append(
                (
                    f"EDI-REC-{i:05d}",
                    tracking_id,
                    f"EDI-INV-{i:04d}",
                    actual_weight,
                    round(expected_cost * 2.4, 2),
                    round(expected_cost * 0.35, 2),
                    round(expected_cost * 2.75, 2),
                    "NEXT_DAY_AIR",
                    billed_zone,
                    dest_zip,
                    delivery_timestamp,
                    invoice_date,
                )
            )
        elif i == 4:
            # Same tracking billed on both channels (Gate 4).
            billed_weight = round(actual_weight + 0.1, 2)
            base_rate = round(expected_cost * 0.85, 2)
            fuel = round(expected_cost * 0.15, 2)
            total_billed = round(base_rate + fuel, 2)
            edi_invoice = f"EDI-INV-{i:04d}"
            portal_invoice = f"INV-PORTAL-{i:04d}"
            edi_rows.append(
                (
                    f"EDI-REC-{i:05d}",
                    tracking_id,
                    edi_invoice,
                    billed_weight,
                    base_rate,
                    fuel,
                    total_billed,
                    billed_service,
                    billed_zone,
                    dest_zip,
                    delivery_timestamp,
                    invoice_date,
                )
            )
            portal_rows.append(
                (
                    tracking_id,
                    portal_invoice,
                    "UPS",
                    billed_weight,
                    base_rate,
                    fuel,
                    total_billed,
                    billed_service,
                    billed_zone,
                    dest_zip,
                    delivery_timestamp,
                    invoice_date + timedelta(days=10),
                    scan_time + timedelta(days=12),
                )
            )
        elif i % 3 == 0:
            portal_invoice = f"INV-PORTAL-{i:04d}"
            portal_rows.append(
                (
                    tracking_id,
                    portal_invoice,
                    "UPS",
                    actual_weight,
                    round(expected_cost * 0.85, 2),
                    round(expected_cost * 0.15, 2),
                    expected_cost,
                    billed_service,
                    billed_zone,
                    dest_zip,
                    delivery_timestamp,
                    invoice_date,
                    scan_time + timedelta(days=2),
                )
            )
        else:
            billed_weight = round(actual_weight + random.uniform(0.0, 0.2), 2)
            base_rate = round(expected_cost * 0.85, 2)
            fuel = round(expected_cost * 0.15, 2)
            total_billed = round(base_rate + fuel, 2)
            edi_rows.append(
                (
                    f"EDI-REC-{i:05d}",
                    tracking_id,
                    f"EDI-INV-{i:04d}",
                    billed_weight,
                    base_rate,
                    fuel,
                    total_billed,
                    billed_service,
                    billed_zone,
                    dest_zip,
                    delivery_timestamp,
                    invoice_date,
                )
            )

        invoice_number = None
        source_channel = None
        if i == 1:
            invoice_number, source_channel = "UPS-994112", "PORTAL_PDF_AI"
        elif i == 2:
            invoice_number, source_channel = "UPS-EDI-883900", "UPS_EDI"
        elif i in (5, 6, 7):
            if any(row[1] == tracking_id for row in edi_rows):
                invoice_number = next(
                    row[2] for row in edi_rows if row[1] == tracking_id
                )
                source_channel = "UPS_EDI"
            elif any(row[0] == tracking_id for row in portal_rows):
                invoice_number = next(
                    row[1] for row in portal_rows if row[0] == tracking_id
                )
                source_channel = "PORTAL_PDF_AI"

        if invoice_number and i == 5:
            accessorial_rows.append(
                (
                    f"ACC-{i:05d}-1",
                    tracking_id,
                    invoice_number,
                    source_channel,
                    1,
                    "RESIDENTIAL",
                    5.20,
                    invoice_date,
                )
            )
        elif invoice_number and i == 6:
            accessorial_rows.append(
                (
                    f"ACC-{i:05d}-1",
                    tracking_id,
                    invoice_number,
                    source_channel,
                    1,
                    "RESIDENTIAL",
                    5.20,
                    invoice_date,
                )
            )
        elif invoice_number and i == 7:
            accessorial_rows.append(
                (
                    f"ACC-{i:05d}-1",
                    tracking_id,
                    invoice_number,
                    source_channel,
                    1,
                    "ADDRESS_CORRECTION",
                    19.00,
                    invoice_date,
                )
            )

    insert_wms_query = """
    INSERT INTO source_enterprise.raw_wms_package_scans (
        package_id, tracking_id, carrier_code, service_level,
        actual_scale_weight, package_length_in, package_width_in, package_height_in,
        origin_warehouse, origin_zip, dest_zip, dest_address_type,
        dest_address_cass_validated, scan_timestamp, dock_departure_timestamp,
        expected_cost
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """

    insert_edi_query = """
    INSERT INTO source_enterprise.raw_edi_ups_invoices (
        edi_record_id, tracking_id, carrier_invoice_id, billed_weight,
        base_rate, fuel_surcharge, total_billed_amount, service_level_billed,
        zone_billed, dest_zip, delivery_timestamp, invoice_date
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """

    insert_portal_query = """
    INSERT INTO source_enterprise.raw_portal_extracted_invoices (
        tracking_id, invoice_number, carrier_name, billed_weight, base_charge,
        fuel_surcharge, grand_total, service_level_billed, zone_billed, dest_zip,
        delivery_timestamp, invoice_date, extraction_timestamp
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """

    insert_accessorial_query = """
    INSERT INTO source_enterprise.raw_carrier_accessorial_charges (
        accessorial_charge_id, tracking_id, invoice_number, source_channel,
        charge_line_number, accessorial_code, accessorial_amount, invoice_date
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(insert_wms_query, wms_rows)
            cur.executemany(insert_edi_query, edi_rows)
            cur.executemany(insert_portal_query, portal_rows)
            cur.executemany(insert_accessorial_query, accessorial_rows)
            conn.commit()

    print("✅ Seeding Complete:")
    print(f"   - WMS Scans: {len(wms_rows)} records")
    print(f"   - UPS EDI Records: {len(edi_rows)} records")
    print(f"   - Portal PDF Records: {len(portal_rows)} records")
    print(f"   - Accessorial Charges: {len(accessorial_rows)} records")


if __name__ == "__main__":
    print("=== INITIALIZING POSTGRESQL MULTI-SOURCE DATABASE ===")
    initialize_schemas_and_tables()
    seed_synthetic_data(num_records=50)
