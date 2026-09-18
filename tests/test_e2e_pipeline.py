import os
from src.privacy.pii_masker import PIIMasker
from src.utils.pdf_extractor import extract_text_from_pdf
from src.agent.math_verifier import MathVerifier
from src.agent.schemas import InvoiceItem, CarrierInvoiceExtraction


def test_pii_masker_redacts_sensitive_info():
    """Verify that PII Masker scrubs emails, phones, and addresses."""
    masker = PIIMasker()
    raw_text = "Customer Alice Smith at alice@test.com phone (555) 123-4567 living at 123 Main St."
    masked, stats = masker.mask(raw_text)

    assert "[REDACTED_EMAIL]" in masked
    assert "[REDACTED_PHONE]" in masked
    assert "[REDACTED_ADDRESS]" in masked
    assert "alice@test.com" not in masked
    assert "(555) 123-4567" not in masked
    assert stats["emails"] == 1
    assert stats["phones"] == 1
    assert stats["addresses"] == 1


def test_pdf_extraction_from_file():
    """Verify that PDF text extractor reads real sample PDF files."""
    sample_pdf_path = "data/sample_valid_invoice.pdf"
    assert os.path.exists(
        sample_pdf_path
    ), "Sample PDF does not exist. Run data/generate_sample_pdfs.py first."

    extracted_text = extract_text_from_pdf(sample_pdf_path)
    assert len(extracted_text) > 50
    assert "UPS-994112" in extracted_text
    assert "1Z9999999999999999" in extracted_text


def test_math_verifier_detects_valid_invoice():
    """Verify that MathVerifier returns is_passed=True for valid totals."""
    verifier = MathVerifier()
    item = InvoiceItem(
        tracking_id="1Z12345",
        billed_weight=10.0,
        base_charge=20.00,
        fuel_surcharge=4.00,
        tax=1.00,
        grand_total=25.00,  # 20.00 + 4.00 + 1.00 = 25.00
    )
    invoice = CarrierInvoiceExtraction(
        invoice_number="INV-001",
        carrier_name="UPS",
        invoice_date="2026-02-10",
        items=[item],
    )

    report = verifier.verify_invoice(invoice)
    assert report.is_passed is True
    assert report.failed_items_count == 0
    assert report.item_results[0].variance == 0.0


def test_math_verifier_flags_corrupted_math():
    """Verify that MathVerifier catches arithmetic discrepancy."""
    verifier = MathVerifier()
    item = InvoiceItem(
        tracking_id="1Z99999",
        billed_weight=5.0,
        base_charge=20.00,
        fuel_surcharge=4.00,
        tax=1.00,
        grand_total=35.00,  # Expected: 25.00, Billed: 35.00 (Variance: $10.00)
    )
    invoice = CarrierInvoiceExtraction(
        invoice_number="INV-002",
        carrier_name="FEDEX",
        invoice_date="2026-02-10",
        items=[item],
    )

    report = verifier.verify_invoice(invoice)
    assert report.is_passed is False
    assert report.failed_items_count == 1
    assert report.item_results[0].variance == 10.00
    assert report.item_results[0].is_valid is False
