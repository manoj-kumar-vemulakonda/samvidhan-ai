"""
Read the text out of uploaded files, page by page.

  PDF with a text layer  -> PyMuPDF reads the text directly (fast, exact)
  scanned PDF / photo    -> OCR on this PC with RapidOCR (free, ~3 s a page)
  hard pages             -> optionally Claude reads the page image instead (Hindi, handwriting,
                            poor scans) - uses your Claude subscription, capped per upload
  Word (.docx)           -> python-docx (split into ~3,500-character "pages")
  text files             -> read as they are

Returns a list of PageText(page, text, method).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

OCR_DPI = 200                  # resolution for OCR (good balance of speed and accuracy)
MIN_TEXT_CHARS = 25            # a PDF page with less real text than this is treated as scanned
PSEUDO_PAGE_CHARS = 3500       # Word / text files are cut into "pages" of about this size
MAX_VISION_PAGES = 12          # at most this many pages per upload are sent to Claude to read
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


@dataclass
class PageText:
    page: int
    text: str
    method: str            # text / ocr / vision / docx / plain
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# OCR (RapidOCR runs on the CPU with ONNX models that come with the package)
# ---------------------------------------------------------------------------
_ocr_engine = None
_ocr_lock = threading.Lock()


def ocr_engine():
    global _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _ocr_engine = RapidOCR()
        return _ocr_engine


def ocr_png(png: bytes, upright: bool = False) -> tuple[str, float]:
    """OCR one page image. Returns (text, average confidence 0..1).
    upright=True skips the text-rotation check (printed gazettes are never upside down): ~40% faster."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return "", 0.0
    engine = ocr_engine()
    with _ocr_lock:  # one page at a time: the engine is not thread-safe
        result, _ = engine(image, use_cls=not upright)
    if not result:
        return "", 0.0
    # Put the pieces back into lines: sort by vertical centre, then left to right
    items = []
    for box, text, score in result:
        if not str(text).strip():
            continue
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append((sum(ys) / len(ys), min(xs), max(ys) - min(ys), str(text).strip(), float(score)))
    if not items:
        return "", 0.0
    items.sort()
    lines: list[list] = []
    for item in items:
        if lines and abs(item[0] - lines[-1][-1][0]) < max(8.0, 0.5 * item[2]):
            lines[-1].append(item)
        else:
            lines.append([item])
    text = "\n".join(" ".join(piece[3] for piece in sorted(line, key=lambda p: p[1])) for line in lines)
    confidence = sum(i[4] for i in items) / len(items)
    return text, confidence


def looks_poor(text: str, confidence: float) -> bool:
    """Should a better reader (Claude) look at this page? Little text, low confidence or glued words."""
    words = re.findall(r"[A-Za-z]+", text)
    if len(text.strip()) < 40 or confidence < 0.80:
        return True
    if words:
        glued = sum(1 for w in words if len(w) >= 16) / len(words)
        if glued > 0.15:
            return True
    return False


def page_png(page, dpi: int = OCR_DPI) -> bytes:
    return page.get_pixmap(dpi=dpi).tobytes("png")


# ---------------------------------------------------------------------------
# Readers per kind of file
# ---------------------------------------------------------------------------
Vision = Callable[[bytes], str]        # png -> text (Claude), or None when not allowed
Progress = Callable[[str], None]


def read_pdf(path: Path, vision: Vision | None, progress: Progress) -> list[PageText]:
    import pymupdf

    pages: list[PageText] = []
    vision_used = 0
    with pymupdf.open(path) as document:
        total = document.page_count
        for index in range(total):
            page = document[index]
            text = page.get_text("text") or ""
            clean = re.sub(r"\s+", " ", text).strip()
            if len(clean) >= MIN_TEXT_CHARS:
                pages.append(PageText(index + 1, text.strip(), "text"))
                continue
            progress(f"Reading scanned page {index + 1} of {total} (OCR)")
            png = page_png(page)
            ocr_text, confidence = ocr_png(png)
            method = "ocr"
            if vision and vision_used < MAX_VISION_PAGES and looks_poor(ocr_text, confidence):
                progress(f"Claude is reading page {index + 1} of {total}")
                try:
                    better = vision(png)
                    vision_used += 1
                    if better and len(better.strip()) >= max(20, len(ocr_text.strip()) // 3):
                        ocr_text, confidence, method = better.strip(), 0.95, "vision"
                except Exception:
                    pass  # keep the local OCR text
            pages.append(PageText(index + 1, ocr_text.strip(), method, confidence))
    return pages


def read_image(path: Path, vision: Vision | None, progress: Progress) -> list[PageText]:
    import pymupdf

    progress("Reading the image (OCR)")
    data = path.read_bytes()
    # Normalise any image format to PNG with PyMuPDF (handles jpg, png, tiff, bmp, webp...)
    try:
        pixmap = pymupdf.Pixmap(data)
        if pixmap.alpha:
            pixmap = pymupdf.Pixmap(pixmap, 0)
        png = pixmap.tobytes("png")
    except Exception:
        png = data
    text, confidence = ocr_png(png)
    method = "ocr"
    if vision and looks_poor(text, confidence):
        progress("Claude is reading the image")
        try:
            better = vision(png)
            if better and len(better.strip()) >= 20:
                text, confidence, method = better.strip(), 0.95, "vision"
        except Exception:
            pass
    return [PageText(1, text.strip(), method, confidence)]


def split_pseudo_pages(text: str) -> list[str]:
    paragraphs = [p for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    pages, current = [], ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) > PSEUDO_PAGE_CHARS:
            pages.append(current)
            current = paragraph
        else:
            current = f"{current}\n{paragraph}" if current else paragraph
    if current or not pages:
        pages.append(current)
    return pages


def read_docx(path: Path) -> list[PageText]:
    import docx  # python-docx

    document = docx.Document(str(path))
    parts: list[str] = []
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(node.text or "" for node in child.iter() if node.tag.endswith("}t"))
            if text.strip():
                parts.append(text)
        elif tag == "tbl":
            for row in child.iter():
                if row.tag.endswith("}tr"):
                    cells = []
                    for cell in row.iter():
                        if cell.tag.endswith("}tc"):
                            cells.append("".join(n.text or "" for n in cell.iter() if n.tag.endswith("}t")).strip())
                    if any(cells):
                        parts.append(" | ".join(cells))
    return [PageText(i, page, "docx") for i, page in enumerate(split_pseudo_pages("\n".join(parts)), start=1)]


def read_text_file(path: Path) -> list[PageText]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            if encoding == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return [PageText(i, page, "plain") for i, page in enumerate(split_pseudo_pages(text), start=1)]


def read_file(path: Path, kind: str, vision: Vision | None = None, progress: Progress | None = None) -> list[PageText]:
    progress = progress or (lambda message: None)
    if kind == "pdf":
        return read_pdf(path, vision, progress)
    if kind == "image":
        return read_image(path, vision, progress)
    if kind == "docx":
        return read_docx(path)
    if kind == "text":
        return read_text_file(path)
    raise ValueError(f"Can't read files of this kind: {path.suffix}")


def language_of(text: str) -> str:
    """'hi' if the text is mostly Devanagari, else 'en' (a rough guide for the AI)."""
    letters = re.findall(r"[A-Za-zऀ-ॿ]", text)
    if not letters:
        return "en"
    return "hi" if sum(1 for ch in letters if DEVANAGARI.match(ch)) / len(letters) > 0.3 else "en"
