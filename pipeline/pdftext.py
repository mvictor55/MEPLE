"""PDF -> text. Uses poppler's pdftotext when installed; otherwise pypdf (fast),
falling back to pdfplumber (slower, sometimes better) when pypdf finds nothing."""
import logging, subprocess
from pathlib import Path

logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

def pdf_to_text(pdf: Path, layout=True) -> str:
    try:
        args = ["pdftotext"] + (["-layout"] if layout else []) + [str(pdf), "-"]
        out = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if out.stdout.strip():
            return out.stdout
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        txt = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(pdf)).pages)
        if txt.strip():
            return txt
    except Exception:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(pdf) as doc:
            return "\n".join((pg.extract_text() or "") for pg in doc.pages)
    except Exception:
        return ""
