import pytest

# Do not import src.agent.llm_agent at module load. That module raises if
# OPENAI_API_KEY is missing, which would crash pytest collection on CI.

pytestmark = pytest.mark.live

EVAL_DATASET = [
    {
        "test_name": "clean_standard_invoice",
        "raw_text": "UPS INV #UPS-101 Date: 2026-02-10 Track: 1Z111111 Weight: 10.0 lbs Base: $40.00 Fuel: $5.00 Total: $45.00",
        "expected_tracking": "1Z111111",
        "expected_weight": 10.0,
        "expected_total": 45.00,
        "should_math_pass": True,
    },
    {
        "test_name": "noisy_spaces_and_casing",
        "raw_text": "FedEx Bill #FDX-202 Date: Feb 11 2026 Tracking: 1z 222 222 Weight: 4.5 lbs Base Rate: $15.00 Fuel: $2.50 Total: $17.50",
        "expected_tracking": "1Z222222",
        "expected_weight": 4.5,
        "expected_total": 17.50,
        "should_math_pass": True,
    },
    {
        "test_name": "arithmetic_hallucination_invoice",
        "raw_text": "DHL Invoice #DHL-303 Track: 1Z333333 Weight: 2.0 lbs Base: $20.00 Fuel: $3.00 Total: $35.00",
        "expected_tracking": "1Z333333",
        "expected_weight": 2.0,
        "expected_total": 35.00,
        "should_math_pass": False,
    },
]


@pytest.fixture(scope="module")
def agent():
    from src.agent.llm_agent import InvoiceExtractionAgent

    return InvoiceExtractionAgent()


@pytest.mark.parametrize("case", EVAL_DATASET, ids=lambda c: c["test_name"])
def test_extraction_precision_and_math_guardrails(agent, case):
    """Field precision plus Defense 2 math grounding on live gpt-4o-mini."""
    extracted_data, pii_stats, math_report = agent.extract(case["raw_text"])

    assert len(extracted_data.items) >= 1
    item = extracted_data.items[0]

    assert item.tracking_id == case["expected_tracking"]
    assert abs(item.billed_weight - case["expected_weight"]) < 0.01
    assert abs(item.grand_total - case["expected_total"]) < 0.01
    assert math_report.is_passed == case["should_math_pass"]
