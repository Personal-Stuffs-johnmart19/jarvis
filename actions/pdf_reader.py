from local_mode.model import generate_content
# actions/pdf_reader.py
# Jarvis — PDF reader + lightweight RAG (extract, chunk, Gemini QA)

import re
import io
import sys
from pathlib import Path
from local_mode.messages import OwnedError, OwnedValueError

try:
    from pypdf import PdfReader
    _PYPdf = True
except ImportError:
    try:
        from PyPDF2 import PdfReader
        _PYPdf = True
    except ImportError:
        _PYPdf = False


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = get_base_dir() / "config" / "api_keys.json"

_SEARCH_DIRS = [
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Desktop",
    Path.home(),
]

MODEL = "gemini-3.6-flash"
CHUNK_SIZE = 2600
TOP_CHUNKS = 3


def _get_api_key() -> str:
    from api_keys import next_key
    return next_key()


def _resolve_pdf(raw: str) -> Path | None:
    """Accept an absolute path, a path relative to base dir, or a bare file name."""
    raw = (raw or "").strip().strip('"')
    if not raw:
        return None

    cand = Path(raw)
    if cand.is_absolute() and cand.exists():
        return cand
    if not cand.suffix:
        cand = cand.with_suffix(".pdf")

    if cand.exists():
        return cand

    for d in _SEARCH_DIRS:
        p = d / cand.name
        if p.exists():
            return p
        if Path.home() in [d]:
            for sub in ("Documents", "Downloads", "Desktop"):
                q = d / sub / cand.name
                if q.exists():
                    return q
    return None


def _extract_text(path: Path) -> str:
    return _extract_pdf(path)[0]


def _extract_pdf(path: Path) -> tuple[str, int]:
    if not _PYPdf:
        raise RuntimeError("pypdf is not installed. Run: pip install pypdf")
    from local_mode.file_policy import checked_open
    from local_mode.documents import MAX_BYTES
    with checked_open(path) as stream:
        data = stream.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise OwnedValueError("Document must contain 1 byte to 12 MiB.")
    return _extract_pdf_data(data)


def _extract_pdf_data(data: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        pages.append(txt)
    return "\n\n".join(pages), len(reader.pages)


def _chunk_text(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]


def _pick_chunks(question: str, chunks: list[str]) -> list[str]:
    words = {w for w in re.findall(r"\w{3,}", question.lower()) if w not in
             {"the", "and", "for", "with", "from", "that", "this", "what",
              "where", "when", "which", "how", "about", "are", "was", "pdf",
              "file", "tell", "does", "did"}}
    scored = []
    for i, chunk in enumerate(chunks):
        low = chunk.lower()
        score = sum(1 for w in words if w in low)
        scored.append((score, i, chunk))
    scored.sort(key=lambda t: t[0], reverse=True)
    picked = [t[2] for t in scored if t[0] > 0][:TOP_CHUNKS]
    if not picked:
        picked = chunks[:1]
    return picked


def _ask_gemini(question: str, context: str) -> str:

    prompt = (
        "You are Jarvis. Answer the user's question using ONLY the document "
        "excerpt below. Be accurate and concise (max 4 sentences). If the answer "
        "is not in the excerpt, say you couldn't find it in the document.\n\n"
        f"DOCUMENT EXCERPT:\n{context}\n\n"
        f"QUESTION: {question}"
    )
    try:
        resp = generate_content(model=MODEL, contents=prompt)
        return (resp.text or "").strip() or "No answer, sir."
    except Exception as e:
        return f"PDF question failed: {e}"


def read_pdf(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Extract and summarize a PDF file."""
    params = parameters or {}
    path   = _resolve_pdf(str(params.get("path", params.get("file", ""))))
    if path is None:
        return ("I couldn't find that PDF, sir. Give me the file name or a path "
                "(e.g. 'manual.pdf' or 'C:\\Users\\you\\Documents\\manual.pdf').")

    try:
        text, pages = _extract_pdf(path)
    except OwnedError:
        raise
    except Exception as e:
        return f"Could not read the PDF: {e}"

    if not text.strip():
        return f"'{path.name}' is a scanned PDF with no extractable text, sir."

    preview = text[:1200] + ("…" if len(text) > 1200 else "")
    return (
        f"'{path.name}' — {pages} pages, about {len(text)} characters.\n\n"
        f"Preview:\n{preview}"
    )


def pdf_qa(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Answer a question about a PDF's contents (lightweight RAG)."""
    params   = parameters or {}
    path     = _resolve_pdf(str(params.get("path", params.get("file", ""))))
    question = str(params.get("question", params.get("query", ""))).strip()

    if path is None:
        return ("I couldn't find that PDF, sir. Give me the file name or a path.")
    if not question:
        return "What would you like to know from the document, sir?"

    try:
        text = _extract_text(path)
    except OwnedError:
        raise
    except Exception as e:
        return f"Could not read the PDF: {e}"

    chunks = _chunk_text(text)
    if not chunks:
        return f"'{path.name}' has no extractable text, sir."
    picked = _pick_chunks(question, chunks)
    return _ask_gemini(question, "\n\n---\n\n".join(picked))
