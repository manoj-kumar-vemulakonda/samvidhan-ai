"""
Files you can take away:
  * Word (.docx) - any answer, draft or workflow result, under your name (letterhead)
  * Excel (.xlsx) - a review table (with risk colours), or the index of a data room
  * Calendar (.ics) - your hearings and deadlines, to import into Google Calendar / Outlook
  * Data room (.zip) - a case's organised folders plus Index.xlsx
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta

from app import config, files, store

RISK_COLOURS = {"high": "F4C7C3", "medium": "FCE8B2", "low": "D9EAD3", "none": "EEEEEE"}


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------
def _add_inline(paragraph, text: str) -> None:
    """**bold** and *italic* inside a line."""
    for piece in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            paragraph.add_run(piece[2:-2]).bold = True
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            paragraph.add_run(piece[1:-1]).italic = True
        else:
            paragraph.add_run(piece)


def to_docx(title: str, markdown: str, settings: dict | None = None, case: dict | None = None) -> bytes:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    settings = settings or store.get_settings()
    document = docx.Document()
    style = document.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)
    # Letterhead
    name = store.display_name(settings)
    if name or settings.get("firm"):
        head = document.add_paragraph()
        head.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = head.add_run(settings.get("firm") or name)
        run.bold = True
        run.font.size = Pt(14)
        details = [d for d in (name if settings.get("firm") else "", settings.get("enrolment") and f"Enrolment No. {settings['enrolment']}",
                               settings.get("address"), settings.get("phone"), settings.get("email")) if d]
        if details:
            line = document.add_paragraph(" | ".join(details))
            line.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if case:
        meta = document.add_paragraph()
        meta.add_run(f"{case['title']}").bold = True
        extra = " · ".join(x for x in (case.get("court"), case.get("case_number"), case.get("cnr")) if x)
        if extra:
            meta.add_run(f"\n{extra}")
    document.add_heading(title, level=1)
    in_list = False
    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            in_list = False
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)", line)
        bullet = re.match(r"^\s*[-*•]\s+(.*)", line)
        numbered = re.match(r"^\s*\d+[.)]\s+(.*)", line)
        if heading:
            document.add_heading(heading.group(2).strip("# "), level=min(4, len(heading.group(1)) + 1))
        elif bullet:
            _add_inline(document.add_paragraph(style="List Bullet"), bullet.group(1))
            in_list = True
        elif numbered:
            _add_inline(document.add_paragraph(style="List Number"), numbered.group(1))
            in_list = True
        elif re.fullmatch(r"\s*[-_*]{3,}\s*", line):
            document.add_paragraph("")
        else:
            _add_inline(document.add_paragraph(), line.strip())
    foot = document.add_paragraph()
    foot.add_run(f"\nPrepared with {config.APP_NAME} on {date.today().strftime('%d %B %Y')}. "
                 "Check every citation and fact before filing.").italic = True
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def save_draft_to_case(case: dict, title: str, content: bytes) -> dict:
    folder = files.case_dir(case) / "08 Drafts"
    folder.mkdir(parents=True, exist_ok=True)
    name = files.safe_name(f"{date.today().isoformat()} {title}", 100) + ".docx"
    path = files.unique_path(folder / name)
    path.write_bytes(content)
    return {"path": files.relative(path), "filename": path.name, "folder": folder.name}


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def review_xlsx(review: dict, documents: dict[int, dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    book = Workbook()
    sheet = book.active
    sheet.title = "Review"
    headers = ["Document", "Type", "Date"] + [c["label"] for c in review["columns"]]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    cells = {(c["document_id"], c["column_key"]): c for c in review["cells"]}
    for document_id in review["document_ids"]:
        document = documents.get(document_id)
        if not document:
            continue
        row = [document["title"], document["doc_type"], document["doc_date"]]
        for column in review["columns"]:
            cell = cells.get((document_id, column["key"]), {})
            text = cell.get("answer", "")
            if cell.get("page"):
                text += f" (p. {cell['page']})"
            row.append(text)
        sheet.append(row)
        for index, column in enumerate(review["columns"], start=4):
            risk = cells.get((document_id, column["key"]), {}).get("risk", "")
            if risk in RISK_COLOURS:
                sheet.cell(row=sheet.max_row, column=index).fill = PatternFill("solid", fgColor=RISK_COLOURS[risk])
    for column_cells in sheet.columns:
        sheet.column_dimensions[column_cells[0].column_letter].width = 40
        for cell in column_cells:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    if review.get("report"):
        summary = book.create_sheet("Report")
        for line in review["report"].splitlines():
            summary.append([line])
        summary.column_dimensions["A"].width = 120
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def index_xlsx(case: dict, documents: list[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    sheet = book.active
    sheet.title = "Index"
    sheet.append([case["title"]])
    sheet["A1"].font = Font(bold=True, size=14)
    sheet.append([" · ".join(x for x in (case.get("court"), case.get("case_number"), case.get("cnr")) if x)])
    sheet.append([])
    sheet.append(["#", "Folder", "File", "Document", "Type", "Date", "Pages", "Summary"])
    for cell in sheet[4]:
        cell.font = Font(bold=True)
    for number, d in enumerate(documents, start=1):
        sheet.append([number, d["folder"], d["filename"], d["title"], d["doc_type"], d["doc_date"], d["pages"], d["summary"]])
    for letter, width in zip("ABCDEFGH", (5, 28, 50, 40, 20, 12, 7, 80)):
        sheet.column_dimensions[letter].width = width
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Data room
# ---------------------------------------------------------------------------
def data_room_zip(case: dict, include_originals: bool = False) -> bytes:
    documents = store.list_documents(case_id=case["id"])
    root = files.case_dir(case)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Index.xlsx", index_xlsx(case, documents))
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if relative.parts[0] == files.ORIGINALS and not include_originals:
                continue
            archive.write(path, relative.as_posix())
    return out.getvalue()


# ---------------------------------------------------------------------------
# Calendar (.ics)
# ---------------------------------------------------------------------------
def _ics_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def calendar_ics(events: list[dict]) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{config.APP_NAME}//Calendar//EN", "CALSCALE:GREGORIAN"]
    for event in events:
        day = date.fromisoformat(event["date"])
        lines += ["BEGIN:VEVENT", f"UID:event-{event['id']}@samvidhan-ai.local", f"DTSTAMP:{stamp}"]
        if event.get("time"):
            hours, minutes = event["time"].split(":")
            start = datetime.combine(day, datetime.min.time()).replace(hour=int(hours), minute=int(minutes))
            end = start + timedelta(hours=1)
            lines += [f"DTSTART;TZID=Asia/Kolkata:{start:%Y%m%dT%H%M%S}", f"DTEND;TZID=Asia/Kolkata:{end:%Y%m%dT%H%M%S}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]
        summary = event["title"] + (f" - {event['case_title']}" if event.get("case_title") else "")
        lines.append(f"SUMMARY:{_ics_escape(summary)}")
        location = event.get("location") or event.get("case_court") or ""
        if location:
            lines.append(f"LOCATION:{_ics_escape(location)}")
        description = " ".join(x for x in (event.get("kind", "").title(), event.get("notes", ""), event.get("quote", "")) if x)
        if description:
            lines.append(f"DESCRIPTION:{_ics_escape(description)}")
        lines += ["BEGIN:VALARM", "TRIGGER:-PT12H", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_escape(summary)}", "END:VALARM",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
