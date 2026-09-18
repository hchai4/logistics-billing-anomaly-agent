import io
from pypdf import PdfReader
from typing import Union


def extract_text_from_pdf(pdf_input: Union[str, bytes, io.BytesIO]) -> str:
    """
    Extracts raw text from a PDF file path, raw bytes, or BytesIO stream.
    Compatible with both local files and Streamlit's UploadedFile object.
    """
    if isinstance(pdf_input, bytes):
        stream = io.BytesIO(pdf_input)
    elif isinstance(pdf_input, str):
        stream = open(pdf_input, "rb")
    else:
        stream = pdf_input

    reader = PdfReader(stream)
    extracted_text = []

    for idx, page in enumerate(reader.pages):
        page_text = page.extract_text()
        if page_text:
            extracted_text.append(page_text)

    combined_text = "\n".join(extracted_text).strip()

    if not combined_text:
        raise ValueError(
            "The provided PDF contains no extractable text (it might be a scanned image)."
        )

    return combined_text


if __name__ == "__main__":
    print("PDF Extractor module initialized successfully.")
