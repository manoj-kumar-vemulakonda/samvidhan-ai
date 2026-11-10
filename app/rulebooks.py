"""
Rule books the lawyer adds from the Library page - their own High Court's rules, a Civil Rules of Practice,
a practice direction - so the AI can cite them like any Act: [Kerala HC Rules r. 45].

    start(pdf_bytes, filename, name="Rules of the High Court of Kerala, 1971", cite="Kerala HC Rules",
          court="Kerala High Court", jurisdiction="Kerala")      -> job id; reading runs in the background
    remove("MY_KERALAHCRULES")

Files live in <workspace>/library/: CODE.pdf (the original) and CODE.json (the provisions). The library and
the search index are reloaded afterwards; only the new pieces are embedded.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from app import rulebook
from app.bus import bus
from app.laws import reload_library, user_library_dir

NUMBERINGS = {"auto": "rules", "rules": "rules", "chapters": "chapters", "orders": "orders", "sections": "sections",
              "paras": "paras", "pages": "pages"}
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def jobs() -> list[dict]:
    with _lock:
        return [dict(j) for j in _jobs.values()]


def make_code(cite: str) -> str:
    base = re.sub(r"[^A-Z0-9]", "", cite.upper())[:24] or "BOOK"
    code, n = f"MY_{base}", 2
    folder = user_library_dir()
    from app.laws import library
    while (folder / f"{code}.json").exists() or code in library().acts:
        code, n = f"MY_{base}{n}", n + 1
    return code


def _set(code: str, **fields) -> None:
    with _lock:
        _jobs.setdefault(code, {"code": code}).update(fields)
        snapshot = dict(_jobs[code])
    bus.publish("library", snapshot)


def start(content: bytes, filename: str, name: str, cite: str, court: str = "", jurisdiction: str = "",
          numbering: str = "auto", category: str = "My rule books", on_done=None) -> str:
    """Save the PDF and read it in a background thread. Returns the book's code."""
    if not content.startswith(b"%PDF"):
        raise ValueError("Please choose a PDF file.")
    name = re.sub(r"\s+", " ", name).strip()[:200] or Path(filename).stem
    cite = re.sub(r"\s+", " ", cite).strip()[:60] or name[:40]
    folder = user_library_dir()
    folder.mkdir(parents=True, exist_ok=True)
    code = make_code(cite)
    (folder / f"{code}.pdf").write_bytes(content)
    _set(code, name=name, cite=cite, status="reading", progress=0.0, message="Reading the PDF...", started=time.time())
    book = {"code": code, "name": name, "cite": cite, "court": court.strip()[:120], "jurisdiction": jurisdiction.strip()[:80],
            "numbering": NUMBERINGS.get(numbering, "rules"), "category": category, "filename": filename}
    threading.Thread(target=_build, args=(book, on_done), daemon=True, name=f"rulebook-{code}").start()
    return code


def start_text(text: str, name: str, cite: str, court: str = "", jurisdiction: str = "", numbering: str = "auto",
               category: str = "My rule books", on_done=None) -> str:
    """Add a rule book from pasted text (when no PDF exists): split into rules like a PDF would be."""
    text = (text or "").replace("\r\n", "\n").strip()
    if len(text) < 200:
        raise ValueError("Paste the full text of the rules (at least a few paragraphs).")
    name = re.sub(r"\s+", " ", name).strip()[:200] or "Rule book"
    cite = re.sub(r"\s+", " ", cite).strip()[:60] or name[:40]
    folder = user_library_dir()
    folder.mkdir(parents=True, exist_ok=True)
    code = make_code(cite)
    (folder / f"{code}.txt").write_text(text, encoding="utf-8")
    _set(code, name=name, cite=cite, status="reading", progress=0.0, message="Reading the text...", started=time.time())
    book = {"code": code, "name": name, "cite": cite, "court": court.strip()[:120], "jurisdiction": jurisdiction.strip()[:80],
            "numbering": NUMBERINGS.get(numbering, "rules"), "category": category, "filename": "pasted text", "text": True}
    threading.Thread(target=_build, args=(book, on_done), daemon=True, name=f"rulebook-{code}").start()
    return code


def _text_lines(text: str) -> list:
    """Pasted text -> lines on pseudo-pages of about 3,000 characters (so answers can still point to a part)."""
    lines, page, size = [], 1, 0
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if size > 3000 and rulebook.PROVISION.match(line):
            page, size = page + 1, 0
        lines.append(rulebook.Line(line, page))
        size += len(line)
    return lines


def _build(book: dict, on_done) -> None:
    code = book["code"]
    folder = user_library_dir()
    pdf = folder / f"{code}.pdf"
    try:
        def progress(done: int, total: int) -> None:
            _set(code, progress=round(done / max(1, total) * 0.8, 3), message=f"Reading page {done} of {total}...")

        if book.get("text"):
            lines = _text_lines((folder / f"{code}.txt").read_text(encoding="utf-8"))
        else:
            lines = rulebook.pdf_lines(pdf, ocr="auto", progress=progress)
        if book["numbering"] == "pages":
            provisions, how = rulebook.page_provisions(lines), "pages"
        else:
            provisions, how = rulebook.parse_or_pages(lines, numbering=book["numbering"])
        if not provisions:
            raise RuntimeError("No readable text was found in this PDF.")
        record = {
            "code": code, "cite": book["cite"], "name": book["name"], "short": book["cite"], "year": _year(book["name"]),
            "category": book["category"], "status": "in_force", "replaced_by": None, "replaces": None,
            "note": "Added by you from a PDF. Check important rules against the official text.",
            "aliases": sorted({book["name"].lower(), book["cite"].lower()}),
            "source": "Text you pasted" if book.get("text") else f"Your PDF ({book['filename']})",
            "kind": "rules", "court": book["court"], "jurisdiction": book["jurisdiction"] or "",
            "pdf": "" if book.get("text") else pdf.name, "url": "", "as_on": "", "parsed_as": how,
            "sections": [{"number": p["number"], "ref": p["ref"], "kind": p.get("kind", "rule"), "title": p["title"][:200],
                          "chapter": p.get("chapter", ""), "chapter_title": p.get("chapter_title", ""), "text": p["text"],
                          "status": p.get("status", "in_force"), "page": p.get("page")} for p in provisions],
        }
        (folder / f"{code}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        _set(code, progress=0.9, message="Adding it to the library...")
        search = reload_library()
        if on_done:
            on_done(search)
        how_text = {"pages": "one entry per page (no rule numbers found)", "orders": "Orders and rules",
                    "chapters": "chapters and rules", "rules": "rules", "sections": "sections", "paras": "paragraphs"}[how]
        _set(code, status="done", progress=1.0, count=len(provisions), how=how,
             message=f"Added {len(provisions)} entries ({how_text}).")
    except Exception as error:
        _set(code, status="failed", message=f"Could not read this PDF: {error}")
        for leftover in (folder / f"{code}.json",):
            leftover.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# The inbox: save rule-book PDFs into <workspace>/library/inbox and they are added automatically
# ---------------------------------------------------------------------------
COURT_WORDS = [
    (("bombay", "maharashtra", "goa"), "Bombay High Court", "Maharashtra; Goa"),
    (("madras", "tamil nadu", "tamilnadu"), "Madras High Court", "Tamil Nadu; Puducherry"),
    (("calcutta", "west bengal", "kolkata"), "Calcutta High Court", "West Bengal"),
    (("kerala",), "Kerala High Court", "Kerala"),
    (("punjab", "haryana", "chandigarh"), "Punjab and Haryana High Court", "Punjab; Haryana; Chandigarh"),
    (("karnataka",), "Karnataka High Court", "Karnataka"),
    (("madhya pradesh", "mphc", "jabalpur"), "Madhya Pradesh High Court", "Madhya Pradesh"),
    (("patna", "bihar"), "Patna High Court", "Bihar"),
    (("gauhati", "guwahati", "assam"), "Gauhati High Court", "Assam; Nagaland; Mizoram; Arunachal Pradesh"),
    (("telangana",), "Telangana High Court", "Telangana"),
    (("andhra",), "Andhra Pradesh High Court", "Andhra Pradesh"),
    (("sikkim",), "Sikkim High Court", "Sikkim"),
    (("tripura",), "Tripura High Court", "Tripura"),
    (("uttarakhand", "nainital"), "Uttarakhand High Court", "Uttarakhand"),
    (("allahabad", "uttar pradesh"), "Allahabad High Court", "Uttar Pradesh"),
    (("delhi",), "Delhi High Court", "Delhi"),
    (("gujarat",), "Gujarat High Court", "Gujarat"),
    (("rajasthan",), "Rajasthan High Court", "Rajasthan"),
    (("orissa", "odisha"), "Orissa High Court", "Odisha"),
    (("jharkhand",), "Jharkhand High Court", "Jharkhand"),
    (("chhattisgarh",), "Chhattisgarh High Court", "Chhattisgarh"),
    (("himachal",), "Himachal Pradesh High Court", "Himachal Pradesh"),
    (("manipur",), "Manipur High Court", "Manipur"),
    (("meghalaya",), "Meghalaya High Court", "Meghalaya"),
    (("jammu", "kashmir", "ladakh"), "Jammu & Kashmir and Ladakh High Court", "Jammu and Kashmir; Ladakh"),
    (("supreme court",), "Supreme Court of India", "Central"),
]


def inbox_dir() -> Path:
    return user_library_dir() / "inbox"


def guess_book(filename: str) -> dict:
    """'Bombay High Court Appellate Side Rules 1960.pdf' -> name, short citation, court, State."""
    name = re.sub(r"[_\-]+", " ", Path(filename).stem)
    name = re.sub(r"\s+", " ", name).strip() or "Rule book"
    lower = name.lower()
    court, state = "", ""
    for words, court_name, jurisdiction in COURT_WORDS:
        if any(word in lower for word in words):
            court, state = court_name, jurisdiction
            break
    # Rules of Practice / Civil and Criminal Court Rules govern the district courts too
    if court and court != "Supreme Court of India" and re.search(
            r"(?i)rules of practice|civil court|criminal court|subordinate|district|manual", name):
        court += "; District courts"
    cite = re.sub(r"(?i)\bhigh court\b", "HC", name)
    cite = re.sub(r"(?i)^the\s+", "", cite)
    if len(cite) > 45:
        cite = cite[:45].rsplit(" ", 1)[0]
    return {"name": name, "cite": cite, "court": court, "jurisdiction": state}


def scan_inbox(on_done=None) -> list[str]:
    """Start reading every PDF waiting in the inbox (files still being saved are left for the next look)."""
    folder = inbox_dir()
    folder.mkdir(parents=True, exist_ok=True)
    done_dir = folder / "_imported"
    started = []
    for pdf in sorted(folder.glob("*.pdf")):
        try:
            if time.time() - pdf.stat().st_mtime < 5:      # still downloading
                continue
            content = pdf.read_bytes()
        except OSError:
            continue
        done_dir.mkdir(exist_ok=True)
        target = done_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{pdf.name}"
        try:
            shutil.move(str(pdf), str(target))
        except OSError:
            continue
        guess = guess_book(pdf.name)
        try:
            started.append(start(content, pdf.name, guess["name"], guess["cite"], guess["court"], guess["jurisdiction"],
                                 on_done=on_done))
        except ValueError as error:
            bus.publish("library", {"code": "", "name": pdf.name, "status": "failed", "message": str(error)})
    return started


def watch_inbox(stop: threading.Event, on_done=None, every: float = 20.0) -> None:
    """Background loop used by the app: look in the inbox every 20 seconds."""
    while not stop.is_set():
        try:
            scan_inbox(on_done)
        except Exception as error:   # never let the watcher die
            print(f"Rule-book inbox: {error}")
        stop.wait(every)


def _year(name: str) -> int:
    years = re.findall(r"\b(1[89]\d\d|20\d\d)\b", name)
    return int(years[-1]) if years else 0


def remove(code: str, on_done=None) -> bool:
    """Take a rule book you added out of the library (its files go to library/_removed)."""
    folder = user_library_dir()
    if not code.startswith("MY_") or not (folder / f"{code}.json").exists():
        return False
    bin_dir = folder / "_removed"
    bin_dir.mkdir(exist_ok=True)
    for path in folder.glob(f"{code}.*"):
        shutil.move(str(path), str(bin_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{path.name}"))
    with _lock:
        _jobs.pop(code, None)
    search = reload_library()
    if on_done:
        on_done(search)
    bus.publish("library", {"code": code, "status": "removed"})
    return True
