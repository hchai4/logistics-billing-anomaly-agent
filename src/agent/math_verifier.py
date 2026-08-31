from pydantic import BaseModel, Field
from typing import List, Dict, Any
from src.agent.schemas import CarrierInvoiceExtraction, InvoiceItem


class ItemMathResult(BaseModel):
    """
    Verification report for a single invoice line item.
    """
    tracking_id: str
    calculated_total: float
    extracted_total: float
    variance: float
    is_valid: bool


class MathVerificationReport(BaseModel):
    """
    Master verification report for an entire invoice.
    """
    is_passed: bool
    total_items_checked: int
    failed_items_count: int
    item_results: List[ItemMathResult]


class MathVerifier:
    """
    Defense 2: Deterministic Python Math Verification Engine.
    Executes hard arithmetic checks across extracted invoice line items.
    """

    def __init__(self, tolerance: float = 0.01):
        # 0.01 USD tolerance handles minor floating-point rounding issues
        self.tolerance = tolerance

    def verify_item(self, item: InvoiceItem) -> ItemMathResult:
        """
        Verifies arithmetic for a single line item:
        base_charge + fuel_surcharge + tax == grand_total
        """
        calculated_total = round(
            item.base_charge + item.fuel_surcharge + item.tax, 2
        )
        extracted_total = round(item.grand_total, 2)
        variance = round(abs(calculated_total - extracted_total), 2)
        is_valid = variance <= self.tolerance

        return ItemMathResult(
            tracking_id=item.tracking_id,
            calculated_total=calculated_total,
            extracted_total=extracted_total,
            variance=variance,
            is_valid=is_valid
        )

    def verify_invoice(self, invoice: CarrierInvoiceExtraction) -> MathVerificationReport:
        """
        Verifies arithmetic across all line items in an extracted invoice.
        """
        item_results: List[ItemMathResult] = []
        failed_count = 0

        for item in invoice.items:
            result = self.verify_item(item)
            item_results.append(result)
            if not result.is_valid:
                failed_count += 1

        is_passed = failed_count == 0

        return MathVerificationReport(
            is_passed=is_passed,
            total_items_checked=len(invoice.items),
            failed_items_count=failed_count,
            item_results=item_results
        )


if __name__ == "__main__":
    # Local Test Execution
    from src.agent.schemas import InvoiceItem, CarrierInvoiceExtraction

    # 1. Test Case: Valid Line Item
    valid_item = InvoiceItem(
        tracking_id="1Z9999999999999999",
        billed_weight=10.0,
        base_charge=45.00,
        fuel_surcharge=8.50,
        tax=0.00,
        grand_total=53.50  # 45.00 + 8.50 == 53.50 (CORRECT)
    )

    # 2. Test Case: Hallucinated/Corrupted Line Item
    corrupted_item = InvoiceItem(
        tracking_id="1Z8888888888888888",
        billed_weight=5.0,
        base_charge=20.00,
        fuel_surcharge=3.00,
        tax=1.00,
        grand_total=30.00  # 20.00 + 3.00 + 1.00 = 24.00, NOT 30.00 (HALLUCINATED)
    )

    test_invoice = CarrierInvoiceExtraction(
        invoice_number="TEST-123",
        carrier_name="UPS",
        invoice_date="2026-02-10",
        items=[valid_item, corrupted_item]
    )

    verifier = MathVerifier()
    report = verifier.verify_invoice(test_invoice)

    print("=== DEFENSE 2: MATH VERIFICATION TEST ===")
    print(f"Overall Passed: {report.is_passed}")
    print(f"Total Items Checked: {report.total_items_checked}")
    print(f"Failed Items: {report.failed_items_count}\n")

    for res in report.item_results:
        status = "PASSED" if res.is_valid else "FAILED"
        print(f"Tracking ID: {res.tracking_id} | Status: {status}")
        print(f"  - Extracted Total: ${res.extracted_total}")
        print(f"  - Calculated Total: ${res.calculated_total}")
        print(f"  - Variance: ${res.variance}\n")