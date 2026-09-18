from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from typing import Dict

from src.agent.llm_agent import InvoiceExtractionAgent
from src.agent.schemas import CarrierInvoiceExtraction
from src.agent.math_verifier import MathVerificationReport
from src.agent.dispute_agent import DisputeAgent, DisputeRequest, DisputeResponse

# Initialize FastAPI App
app = FastAPI(
    title="Logistics Billing Anomaly & AI Agent API",
    description="Enterprise REST API for PII Masking, Type-Safe LLM Invoice Extraction, Math Verification, and Dispute Generation.",
    version="1.0.0",
)

# Instantiate Agents
extraction_agent = InvoiceExtractionAgent()
dispute_agent = DisputeAgent()


# Request/Response Schemas for Endpoints
class ExtractRequest(BaseModel):
    raw_text: str = Field(
        description="Raw unstructured text or invoice snippet from carrier PDF/email",
        examples=[
            "UPS FREIGHT INVOICE #UPS-994112\nLine Item 1:\nTracking Number: 1z 999 999 9999 9999 99\nBilled Weight: 18.5 lbs\nBase Rate: $45.00\nFuel Surcharge: $8.50\nTotal Item Charge: $53.50"
        ],
    )


class ExtractResponse(BaseModel):
    extracted_data: CarrierInvoiceExtraction
    privacy_stats: Dict[str, int]
    math_verification: MathVerificationReport


@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """Health check endpoint for API monitoring."""
    return {"status": "healthy", "service": "Logistics AI Agent API"}


@app.post(
    "/api/extract", response_model=ExtractResponse, status_code=status.HTTP_200_OK
)
def extract_invoice(payload: ExtractRequest):
    """
    Primary Extraction Endpoint:
    1. Redacts PII (Privacy Layer)
    2. Extracts structured JSON via Instructor + Pydantic (Defense 1)
    3. Runs deterministic Python math checks (Defense 2)
    """
    if not payload.raw_text.strip():
        raise HTTPException(status_code=400, detail="raw_text field cannot be empty.")

    try:
        extracted_data, pii_stats, math_report = extraction_agent.extract(
            payload.raw_text
        )
        return ExtractResponse(
            extracted_data=extracted_data,
            privacy_stats=pii_stats,
            math_verification=math_report,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Extraction pipeline failed: {str(e)}",
        )


@app.post(
    "/api/generate-dispute",
    response_model=DisputeResponse,
    status_code=status.HTTP_200_OK,
)
def generate_dispute(payload: DisputeRequest):
    """
    Dispute Letter Generation Endpoint:
    Generates an executive dispute claim letter for flagged billing anomalies.
    """
    try:
        response = dispute_agent.generate_letter(payload)
        return response
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Dispute generation failed: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn

    # Launch Uvicorn development server on port 8000
    uvicorn.run("src.api.main:app", host="127.0.0.1", port=8000, reload=True)
