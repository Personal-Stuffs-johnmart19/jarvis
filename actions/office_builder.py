import sys
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


OUT_DIR = Path.home() / "Documents" / "Jarvis"


def _sanitize(name: str) -> str:
    name = "".join(c for c in str(name) if c not in '\\/:*?"<>|').strip()
    return name or "document"


def _save_office(document, path: Path) -> None:
    """Serialize before creating a new destination; never replace user files."""
    from local_mode.tools import validate_paths

    validate_paths({"output_path": str(path)})
    with tempfile.TemporaryFile() as staged:
        document.save(staged)
        staged.seek(0)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            shutil.copyfileobj(staged, output, length=64 * 1024)


def create_presentation(title: str = "", slides=None, filename: str = None) -> dict:
    """Create a .pptx from a title and a list of slides.

    slides: list of dicts {"title": str, "bullets": [str]} or plain strings.
    """
    if not title.strip():
        return {"ok": False, "error": "Please provide a presentation title, sir."}
    slides = slides or []

    try:
        from pptx import Presentation

        prs = Presentation()
        title_layout = prs.slide_layouts[0]
        content_layout = prs.slide_layouts[1]

        s = prs.slides.add_slide(title_layout)
        s.shapes.title.text = title
        try:
            s.placeholders[1].text = datetime.now().strftime("%d.%m.%Y")
        except Exception:
            pass

        for idx, item in enumerate(slides, start=1):
            if isinstance(item, str):
                item = {"title": f"Slide {idx}", "bullets": [item]}
            slide_title = str(item.get("title") or f"Slide {idx}")
            bullets = item.get("bullets") or []
            if isinstance(bullets, str):
                bullets = [bullets]

            s = prs.slides.add_slide(content_layout)
            s.shapes.title.text = slide_title
            body = s.placeholders[1].text_frame
            first = True
            for b in bullets:
                p = body.paragraphs[0] if first else body.add_paragraph()
                first = False
                p.text = str(b)
                p.level = 0

        path = OUT_DIR / f"{_sanitize(filename or title)}.pptx"
        _save_office(prs, path)
        return {"ok": True, "result": f"Presentation created: {path}", "path": str(path)}
    except Exception as e:
        return {"ok": False, "error": f"Could not create presentation: {e}"}


def create_spreadsheet(
    filename: str = "spreadsheet",
    headers=None,
    rows=None,
    sheet_name: str = "Sheet1",
) -> dict:
    """Create an .xlsx with optional headers and data rows."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter

    headers = headers or []
    rows = rows or []

    try:
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name

        if headers:
            for c, h in enumerate(headers, start=1):
                cell = ws.cell(row=1, column=c, value=str(h))
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="center")

        start_row = 2 if headers else 1
        for r, row in enumerate(rows, start=start_row):
            if isinstance(row, str):
                row = [row]
            elif isinstance(row, (int, float)):
                row = [row]
            else:
                row = list(row)
            for c, v in enumerate(row, start=1):
                ws.cell(row=r, column=c, value=v)

        max_cols = max(len(headers), *(len(r) if not isinstance(r, (str, int, float)) else 1 for r in rows), 1)
        for c in range(1, max_cols + 1):
            letter = get_column_letter(c)
            ws.column_dimensions[letter].width = 14

        path = OUT_DIR / f"{_sanitize(filename)}.xlsx"
        _save_office(wb, path)
        return {"ok": True, "result": f"Spreadsheet created: {path}", "path": str(path)}
    except Exception as e:
        return {"ok": False, "error": f"Could not create spreadsheet: {e}"}