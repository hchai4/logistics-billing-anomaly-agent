import re

from src.agent.schemas import CarrierInvoiceExtraction

# Demo/editor notes such as "<-- CORRUPTED: ... = 36.50, NOT 45.00"
ANNOTATION_PATTERN = re.compile(r"[ \t]*<--.*$", re.MULTILINE)

STATED_LINE_TOTAL_PATTERN = re.compile(
    r"(?:Total Item Charge|Total Amount Billed|Item Total|Line Total)\s*:\s*\$?\s*"
    r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+\.[0-9]{1,2}|[0-9]+)",
    re.IGNORECASE,
)


def strip_human_annotations(text: str) -> str:
    """Remove inline human comments so they cannot be treated as invoice amounts."""
    return ANNOTATION_PATTERN.sub("", text)


def overlay_printed_line_totals(
    invoice: CarrierInvoiceExtraction, source_text: str
) -> CarrierInvoiceExtraction:
    """
    Replace LLM grand_total values with the printed line totals from source text.

    The math verifier must compare components against what the carrier billed,
    not against a total the model recalculated.
    """
    cleaned = strip_human_annotations(source_text)
    stated_totals = [
        round(float(match.replace(",", "")), 2)
        for match in STATED_LINE_TOTAL_PATTERN.findall(cleaned)
    ]
    if len(stated_totals) != len(invoice.items):
        return invoice

    items = [
        item.model_copy(update={"grand_total": stated})
        for item, stated in zip(invoice.items, stated_totals)
    ]
    return invoice.model_copy(update={"items": items})
