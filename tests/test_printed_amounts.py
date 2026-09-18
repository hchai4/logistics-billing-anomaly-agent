from src.agent.math_verifier import MathVerifier
from src.agent.printed_amounts import (
    overlay_printed_line_totals,
    strip_human_annotations,
)
from src.agent.schemas import CarrierInvoiceExtraction, InvoiceItem


CORRUPTED_INVOICE = """FEDEX EXPRESS INVOICE #FDX-554201
Line Item 1:
Tracking Number: 7949 1122 3344
Billed Weight: 12.0 lbs
Base Rate: $30.00
Fuel Surcharge: $5.00
Tax: $1.50
Total Item Charge: $45.00  <-- CORRUPTED: 30 + 5 + 1.50 = 36.50, NOT 45.00"""


def test_strip_human_annotations_removes_inline_comments():
    cleaned = strip_human_annotations(CORRUPTED_INVOICE)

    assert "CORRUPTED" not in cleaned
    assert "36.50" not in cleaned
    assert "Total Item Charge: $45.00" in cleaned


def test_overlay_replaces_recalculated_total_with_printed_billed_amount():
    llm_corrected = CarrierInvoiceExtraction(
        invoice_number="FDX-554201",
        carrier_name="FEDEX",
        invoice_date="2026-02-11",
        items=[
            InvoiceItem(
                tracking_id="794911223344",
                billed_weight=12.0,
                base_charge=30.00,
                fuel_surcharge=5.00,
                tax=1.50,
                grand_total=36.50,
            )
        ],
    )

    recovered = overlay_printed_line_totals(llm_corrected, CORRUPTED_INVOICE)

    assert recovered.items[0].grand_total == 45.00
    report = MathVerifier().verify_invoice(recovered)
    assert report.is_passed is False
    assert report.item_results[0].variance == 8.50


def test_overlay_is_noop_when_line_total_count_does_not_match():
    invoice = CarrierInvoiceExtraction(
        invoice_number="FDX-554201",
        carrier_name="FEDEX",
        invoice_date="2026-02-11",
        items=[
            InvoiceItem(
                tracking_id="794911223344",
                billed_weight=12.0,
                base_charge=30.00,
                fuel_surcharge=5.00,
                tax=1.50,
                grand_total=36.50,
            )
        ],
    )

    unchanged = overlay_printed_line_totals(invoice, "No line totals here")
    assert unchanged.items[0].grand_total == 36.50
