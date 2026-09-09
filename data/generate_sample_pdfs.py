import os
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def create_invoice_pdf(filename: str, title: str, lines: list):
    os.makedirs("data", exist_ok=True)
    file_path = os.path.join("data", filename)
    c = canvas.Canvas(file_path, pagesize=letter)
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 750, title)
    
    c.setFont("Helvetica", 11)
    y = 710
    for line in lines:
        c.drawString(50, y, line)
        y -= 22
        
    c.save()
    print(f"✅ Generated sample PDF: {file_path}")


if __name__ == "__main__":
    # 1. Valid Invoice PDF (Math passes: 45.00 + 8.50 = 53.50; 12.00 + 2.00 = 14.00)
    valid_lines = [
        "Invoice Number: UPS-994112",
        "Invoice Date: February 10, 2026",
        "Billing Contact: Customer Sarah Smith (email: sarah@ecomm-store.com)",
        "Ship To Address: 123 Industrial Pkwy, Los Angeles, CA 90001",
        "",
        "--- LINE ITEM DETAILS ---",
        "Tracking ID: 1Z9999999999999999",
        "Billed Weight: 18.5 lbs",
        "Base Rate: $45.00",
        "Fuel Surcharge: $8.50",
        "Total Item Charge: $53.50",
        "",
        "Tracking ID: 1Z8888888888888888",
        "Billed Weight: 4.0 lbs",
        "Base Rate: $12.00",
        "Fuel Surcharge: $2.00",
        "Total Item Charge: $14.00"
    ]
    create_invoice_pdf("sample_valid_invoice.pdf", "UPS FREIGHT BILLING STATEMENT", valid_lines)

    # 2. Corrupted Invoice PDF (Math fails: 30.00 + 5.00 + 1.50 = 36.50, but billed $45.00)
    corrupted_lines = [
        "Invoice Number: FDX-554201",
        "Invoice Date: February 11, 2026",
        "Billing Contact: Customer Michael Chang (phone: 555-432-8765)",
        "Ship To Address: 456 Commerce Blvd, Atlanta, GA 30301",
        "",
        "--- LINE ITEM DETAILS ---",
        "Tracking ID: 794911223344",
        "Billed Weight: 12.0 lbs",
        "Base Rate: $30.00",
        "Fuel Surcharge: $5.00",
        "Tax: $1.50",
        "Total Item Charge: $45.00"  # Hallucinated/Corrupted: 30 + 5 + 1.50 = 36.50
    ]
    create_invoice_pdf("sample_corrupted_invoice.pdf", "FEDEX EXPRESS INVOICE", corrupted_lines)