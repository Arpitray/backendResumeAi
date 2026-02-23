import os


def _read_with_pymupdf(path: str) -> str:
    """Primary extractor — handles most PDFs including complex layouts."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        text = ""
        for page in doc:
            page_text = page.get_text("text")
            if page_text:
                text += page_text + "\n"
        doc.close()
        return text
    except ImportError:
        return ""
    except Exception as e:
        print(f"⚠️ PyMuPDF failed: {e}")
        return ""


def _read_with_pdfminer(path: str) -> str:
    """Secondary extractor — handles encoding-heavy PDFs."""
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        return pdfminer_extract(path) or ""
    except ImportError:
        return ""
    except Exception as e:
        print(f"⚠️ pdfminer failed: {e}")
        return ""


def _read_with_pypdf(path: str) -> str:
    """Tertiary extractor — bundled fallback."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text
    except Exception as e:
        print(f"⚠️ pypdf failed: {e}")
        return ""


def read_pdf(path: str) -> str:
    """
    Try multiple PDF extraction strategies in order of reliability.
    Returns the best (longest) non-empty result.
    Raises ValueError if no text could be extracted at all.
    """
    strategies = [
        ("PyMuPDF", _read_with_pymupdf),
        ("pdfminer.six", _read_with_pdfminer),
        ("pypdf", _read_with_pypdf),
    ]

    best_text = ""
    for name, fn in strategies:
        text = fn(path).strip()
        if len(text) > len(best_text):
            best_text = text
        if len(best_text) > 100:          # good-enough threshold
            print(f"✅ PDF extracted via {name} — {len(best_text)} chars")
            return best_text

    if not best_text:
        file_size = os.path.getsize(path) if os.path.exists(path) else 0
        raise ValueError(
            f"Could not extract any text from the PDF ({file_size} bytes). "
            "The file may be scanned/image-only, encrypted, or corrupted."
        )

    print(f"✅ PDF extracted (best effort) — {len(best_text)} chars")
    return best_text


def chunk_text(text: str, size: int = 200) -> list[str]:
    """Split text into word-based chunks of `size` words each."""
    words = text.split()
    chunks = []

    for i in range(0, len(words), size):
        chunk = " ".join(words[i : i + size])
        chunks.append(chunk)

    return chunks
