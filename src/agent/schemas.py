from pydantic import BaseModel, Field, field_validator
from typing import List


class InvoiceItem(BaseModel):
    """
    Schema for an individual shipped package item within a carrier invoice.
    """

    tracking_id: str = Field(
        description="The carrier tracking number (e.g., '1Z9999999999999999')"
    )
    billed_weight: float = Field(
        gt=0, description="Billed weight of the package in lbs. Must be greater than 0."
    )
    base_charge: float = Field(
        ge=0,
        description="Base shipping charge in USD, copied exactly as printed. Do not derive this value.",
    )
    fuel_surcharge: float = Field(
        default=0.0,
        ge=0,
        description="Fuel surcharge in USD copied exactly as printed. Default 0.0 if not listed. Do not derive this value.",
    )
    tax: float = Field(
        default=0.0,
        ge=0,
        description="Tax amount in USD copied exactly as printed. Default 0.0 if not listed. Do not derive this value.",
    )
    grand_total: float = Field(
        gt=0,
        description=(
            "The billed total EXACTLY as printed (e.g. 'Total Item Charge' or "
            "'Total Amount Billed'). Do NOT compute base_charge + fuel_surcharge + tax. "
            "If the printed total disagrees with the components, keep the printed total."
        ),
    )

    @field_validator("tracking_id")
    @classmethod
    def clean_tracking_id(cls, v: str) -> str:
        """Sanitizes the tracking ID string."""
        v = v.strip().replace(" ", "").upper()
        if not v:
            raise ValueError("Tracking ID cannot be empty.")
        return v


class CarrierInvoiceExtraction(BaseModel):
    """
    Master schema for the entire carrier invoice document.
    """

    invoice_number: str = Field(
        description="Unique invoice number identifier (e.g., 'UPS-883912')"
    )
    carrier_name: str = Field(
        description="Name of the shipping carrier (e.g., 'UPS', 'FedEx', 'DHL')"
    )
    invoice_date: str = Field(
        description="Invoice issuance date formatted as YYYY-MM-DD"
    )
    items: List[InvoiceItem] = Field(
        description="List of package items included in this invoice"
    )
