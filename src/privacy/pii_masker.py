import re
import spacy
from typing import Dict, Tuple

# Load light SpaCy model for Named Entity Recognition (NER)
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    raise RuntimeError("SpaCy model 'en_core_web_sm' not found. Run: python -m spacy download en_core_web_sm")


class PIIMasker:
    """
    Ethical AI Privacy Layer: Redacts PII (Emails, Phone Numbers, Names, Addresses)
    from unstructured invoice text before sending to LLM APIs.
    """

    def __init__(self):
        # Regex patterns for deterministic PII
        self.email_pattern = re.compile(
            r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        )
        self.phone_pattern = re.compile(
            r'(\+?\d{1,3}[\s-]?)?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{4}'
        )
        # Match street addresses with optional city/state/ZIP suffixes.
        self.address_pattern = re.compile(
            r'\b\d{1,6}\s+[A-Za-z0-9.\'-]+(?:\s+[A-Za-z0-9.\'-]+){0,6}\s+'
            r'(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|'
            r'Court|Ct|Circle|Cir|Place|Pl|Terrace|Ter|Way|Parkway|Pkwy|Highway|Hwy)\b'
            r'(?:,\s*[A-Za-z.\'-]+(?:\s+[A-Za-z.\'-]+){0,4})?'
            r'(?:,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)?',
            re.IGNORECASE
        )

    def mask_deterministic_pii(self, text: str) -> str:
        """Masks emails, phone numbers, and street addresses using regex."""
        text = self.email_pattern.sub("[REDACTED_EMAIL]", text)
        text = self.phone_pattern.sub("[REDACTED_PHONE]", text)
        text = self.address_pattern.sub("[REDACTED_ADDRESS]", text)
        return text

# ?????
    def mask_named_entities(self, text: str) -> str:
        """Uses SpaCy NER to redact Person Names and Organizations if needed."""
        doc = nlp(text)
        masked_text = text
        
        # Redact PERSON entities from end to start to preserve string indices
        for ent in sorted(doc.ents, key=lambda x: x.start_char, reverse=True):
            if ent.label_ in ["PERSON"]:
                start = ent.start_char
                end = ent.end_char
                masked_text = masked_text[:start] + "[REDACTED_NAME]" + masked_text[end:]
                
        return masked_text

    def mask(self, raw_text: str) -> Tuple[str, Dict[str, int]]:
        """
        Main entry point. Redacts all PII and returns the clean text + metadata.
        """
        masked = self.mask_deterministic_pii(raw_text)
        masked = self.mask_named_entities(masked)

        # Count total redactions for audit logging
        counts = {
            "emails": len(self.email_pattern.findall(raw_text)),
            "phones": len(self.phone_pattern.findall(raw_text)),
            "addresses": len(self.address_pattern.findall(raw_text)),
        }
        
        return masked, counts

if __name__ == "__main__":
    # Test script with a raw sample invoice containing sensitive PII
    sample_invoice = """
    UPS FREIGHT INVOICE #UPS-883912
    Date: 2026-02-10
    Bill To: Customer John Doe
    Contact Email: john.doe@acme-ecomm.com | Phone: (555) 234-5678
    Ship To Address: 742 Evergreen Terrace, Springfield, OR 97477
    
    Item Details:
    Tracking ID: 1Z9999999999999999
    Billed Weight: 18.5 lbs
    Base Charge: $45.00
    Fuel Surcharge: $8.50
    Total Amount Billed: $53.50
    """

    masker = PIIMasker()
    masked_text, redaction_stats = masker.mask(sample_invoice)

    print("=== RAW INVOICE TEXT ===")
    print(sample_invoice)
    print("\n=== MASKED INVOICE TEXT (PRIVACY PROTECTED) ===")
    print(masked_text)
    print("\n=== REDACTION AUDIT METRICS ===")
    print(redaction_stats)