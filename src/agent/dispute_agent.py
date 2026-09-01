import os
import instructor
from openai import OpenAI
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
client = instructor.from_openai(OpenAI(api_key=api_key))


class DisputeRequest(BaseModel):
    """
    Input schema for requesting an automated dispute claim letter.
    """
    invoice_number: str = Field(description="Carrier invoice identifier")
    carrier_name: str = Field(description="Name of the shipping carrier (UPS, FedEx, DHL)")
    tracking_id: str = Field(description="Package tracking number")
    billed_weight: float = Field(description="Weight billed by carrier in lbs")
    actual_weight: float = Field(description="Actual weight recorded by WMS scale in lbs")
    billed_cost: float = Field(description="Total cost billed by carrier in USD")
    expected_cost: float = Field(description="Expected cost based on WMS scale weight in USD")
    dollar_variance: float = Field(description="Overcharge amount requested for refund in USD")


class DisputeResponse(BaseModel):
    """
    Output schema containing the generated dispute letter and metadata.
    """
    tracking_id: str
    dispute_letter: str = Field(description="Formal executive dispute letter to carrier billing support")
    claim_amount: float = Field(description="Total dollar refund claimed")


class DisputeAgent:
    """
    Agent responsible for generating formal carrier dispute letters based on audit anomalies.
    """

    def __init__(self, model_name: str = "gpt-4o-mini"):
        self.model_name = model_name

    def generate_letter(self, req: DisputeRequest) -> DisputeResponse:
        system_prompt = (
            "You are a professional logistics billing audit manager. "
            "Your job is to draft concise, firm, and formal carrier dispute letters "
            "requesting credit refunds for shipping overcharges. "
            "Reference exact invoice numbers, tracking IDs, WMS scale weights vs billed weights, "
            "and requested refund amounts."
        )

        user_prompt = (
            f"Draft a formal dispute letter for Invoice #{req.invoice_number} ({req.carrier_name}).\n"
            f"- Tracking ID: {req.tracking_id}\n"
            f"- Carrier Billed Weight: {req.billed_weight} lbs (${req.billed_cost})\n"
            f"- WMS Scale Recorded Weight: {req.actual_weight} lbs (${req.expected_cost})\n"
            f"- Overcharge Refund Claim: ${req.dollar_variance}"
        )

        response: DisputeResponse = client.chat.completions.create(
            model=self.model_name,
            response_model=DisputeResponse,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2
        )

        return response