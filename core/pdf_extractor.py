"""
core/pdf_extractor.py
Extracts text from invoices/SOFs.
- Tries pdfplumber first (text-based PDFs).
- Falls back to OCR (pytesseract) for image-based PDFs.
"""

import os
import logging
from pathlib import Path
from typing import Dict, Optional, Any

import pdfplumber
import pytesseract
from PIL import Image
import fitz  # PyMuPDF

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set Tesseract path globally
TESSERACT_PATH = os.getenv("TESSERACT_PATH")
if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

def extract_text_pdfplumber(pdf_path: Path) -> str:
    """Extract text from a text-based PDF using pdfplumber."""
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except pdfplumber.PasswordError:
        logger.error(f"PDF is password protected: {pdf_path}")
        raise PermissionError(f"PDF is password protected and cannot be read: {pdf_path}")
    except Exception as e:
        # Log the error but allow the fallback mechanism to handle it
        logger.warning(f"pdfplumber encountered an issue (file might be corrupt/image-based): {e}")
        return "" # Return empty string to trigger OCR fallback
    return text.strip()

def extract_text_ocr(pdf_path: Path, lang: str = "eng") -> str:
    """Extract text from an image-based PDF using OCR."""
    text = ""
    doc = fitz.open(pdf_path)
    
    try:
        for page in doc:
            # Render page to image at 300 DPI
            mat = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # Perform OCR
            page_text = pytesseract.image_to_string(img, lang=lang)
            text += page_text + "\n"
    finally:
        doc.close()
        
    return text.strip()

def extract_text(
    pdf_path: str, 
    min_text_length: int = 50, 
    ocr_lang: str = "eng"
) -> Dict[str, Any]:
    """
    Main extraction function.
    
    Args:
        pdf_path (str): Path to the PDF file.
        min_text_length (int): Threshold to decide if pdfplumber found enough text.
        ocr_lang (str): Language code for Tesseract (e.g., 'eng', 'spa+eng').
                        The MAIN APP should determine this based on Port/Country.
        
    Returns:
        dict: { 'text': str, 'method': str, 'pages': int, 'path': str }
    """
    pdf_path = Path(pdf_path)
    
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # 1. Try pdfplumber
    logger.info(f"Attempting extraction for: {pdf_path.name}")
    try:
        text = extract_text_pdfplumber(pdf_path)
        method = "pdfplumber"
    except PermissionError:
        # Re-raise password errors immediately so the app can alert the user
        raise
    except Exception as e:
        # If pdfplumber crashes hard, treat as empty text and try OCR
        logger.warning(f"pdfplumber failed unexpectedly, switching to OCR. Error: {e}")
        text = ""
        method = "ocr"

    # 2. Check if text is too short (likely scanned/image-based)
    if len(text) < min_text_length:
        logger.info(f"Text too short ({len(text)} chars), switching to OCR ({ocr_lang})...")
        text = extract_text_ocr(pdf_path, lang=ocr_lang)
        method = "ocr"

    # 3. Get page count (with safe fallback)
    page_count = 0
    try:
        # First attempt: pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
    except Exception:
        logger.warning("pdfplumber failed to count pages. Trying fitz fallback...")
        try:
            # Second attempt: fitz (more robust for some corrupt files)
            doc = fitz.open(pdf_path)
            page_count = doc.page_count
            doc.close()
        except Exception as e:
            # If both fail, log it and default to 0. Don't crash the whole process.
            logger.error(f"Failed to count pages with both libraries: {e}")
            page_count = 0

    return {
        "text": text,
        "method": method,
        "pages": page_count,
        "path": str(pdf_path)
    }

# ── Logic to simulate the "Main App" handling ───────────────────────
def get_ocr_lang_for_port(port_name: str) -> str:
    """
    Example of how the Main App decides the language.
    This function would live in your main application logic, not here.
    """
    port_name = port_name.lower()
    if "algeciras" in port_name or "ceuta" in port_name or "valencia" in port_name:
        return "spa+eng"
    elif "barcelona" in port_name:
        return "spa+eng+cat" # Catalan support example
    elif "tanger" in port_name:
        return "fra+ara+eng" # French/Arabic/English example
    else:
        return "eng" # Default fallback

# ── Quick test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python core/pdf_extractor.py <path_to_pdf> [port_name]")
        print("\nRunning self-test...")
        print(f"Tesseract Path: {TESSERACT_PATH}")
        try:
            print(f"Tesseract Version: {pytesseract.get_tesseract_version()}")
            print("✅ Setup verified")
        except Exception as e:
            print(f"❌ Tesseract Error: {e}")
    else:
        try:
            pdf_file = sys.argv[1]
            # Simulating the Main App logic:
            # If a port is provided as a 2nd argument, use it to set language.
            # Otherwise, default to English.
            port_arg = sys.argv[2] if len(sys.argv) > 2 else "Default"
            
            ocr_language = get_ocr_lang_for_port(port_arg)
            
            print(f"--- Processing for Port: {port_arg} (OCR Lang: {ocr_language}) ---")
            
            result = extract_text(pdf_file, ocr_lang=ocr_language)
            
            print(f"Method used : {result['method']}")
            print(f"Pages       : {result['pages']}")
            print(f"Text length : {len(result['text'])} chars")
            print(f"\n--- First 500 chars ---\n{result['text'][:500]}")
            
        except Exception as e:
            print(f"Failed to process file: {e}")

            