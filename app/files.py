"""
Everything that touches files on disk: safe file names, case folders, saving uploads,
splitting PDFs and placing documents in their folders.

Layout inside the workspace folder (D:\\lawyers-ai\\workspace):
    cases\\State v Ramesh Kumar (3)\\
        _originals\\                      every upload exactly as received (never changed)
        03 Orders & Judgments\\2026-11-02 Order - Bail application listed.pdf
        ...
    general\\chat-12\\                     files dropped into a general chat

Paths are stored in the database RELATIVE to the workspace folder, with "/" separators,
and every path is checked to stay inside the workspace (so a strange file name can never
write somewhere else on your PC).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO

from app import config

ORIGINALS = "_originals"

# The standard folders of a case file (the AI picks from these; you can add others)
DEFAULT_FOLDERS = [
    "01 Pleadings & Petitions",
    "02 Applications",
    "03 Orders & Judgments",
    "04 Police Records",
    "05 Evidence & Exhibits",
    "06 Notices & Correspondence",
    "07 Client Documents",
    "08 Drafts",
    "09 Research & Notes",
    "99 Other",
]

KINDS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "text", ".md": "text", ".csv": "text", ".eml": "text", ".rtf": "text",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
    ".tif": "image", ".tiff": "image", ".bmp": "image",
}
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def workspace() -> Path:
    return config.WORKSPACE_DIR


def file_kind(name: str) -> str | None:
    """'pdf', 'docx', 'image', 'text' - or None if we can't read that kind of file."""
    return KINDS.get(Path(name).suffix.lower())


def safe_name(name: str, max_length: int = 110) -> str:
    """A file or folder name that is safe on Windows: no \\ / : * ? " < > |, not too long."""
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', " ", str(name or ""))
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "Untitled"
    stem, dot, suffix = name.rpartition(".")
    if dot and 1 <= len(suffix) <= 5 and suffix.isalnum() and len(name) > max_length:
        name = stem[: max_length - len(suffix) - 1].rstrip(" .") + "." + suffix
    elif len(name) > max_length:
        name = name[:max_length].rstrip(" .")
    if name.split(".")[0].upper() in WINDOWS_RESERVED:
        name = "_" + name
    return name


def safe_folder(name: str) -> str:
    """A folder name for the plan (one level only, no path tricks)."""
    name = safe_name(str(name or "").replace("..", " "), 60)
    return name or "99 Other"


def resolve(relative: str) -> Path:
    """Workspace-relative path -> full path, refusing anything outside the workspace."""
    root = workspace().resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"Path is outside the workspace: {relative}")
    return path


def relative(path: Path) -> str:
    return path.resolve().relative_to(workspace().resolve()).as_posix()


def unique_path(path: Path) -> Path:
    """'Order.pdf' -> 'Order (2).pdf' if the name is taken."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(path)


def case_folder_name(title: str, case_id: int) -> str:
    return f"{safe_name(title, 60)} ({case_id})"


def case_dir(case: dict) -> Path:
    folder = case.get("folder") or case_folder_name(case.get("title", "Case"), case["id"])
    path = resolve(f"cases/{folder}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def chat_dir(chat_id: int) -> Path:
    path = resolve(f"general/chat-{int(chat_id)}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def originals_dir(case: dict | None, chat_id: int | None) -> Path:
    base = case_dir(case) if case else chat_dir(chat_id or 0)
    path = base / ORIGINALS
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload(source: BinaryIO, folder: Path, original_name: str, max_bytes: int) -> tuple[Path, int, str]:
    """Copy an uploaded file into `folder`; returns (path, size, sha256). Stops at max_bytes."""
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder / safe_name(Path(original_name).name))
    digest = hashlib.sha256()
    size = 0
    try:
        with open(target, "wb") as out:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise ValueError(f"{original_name} is larger than {max_bytes // (1024 * 1024)} MB.")
                digest.update(block)
                out.write(block)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target, size, digest.hexdigest()


def pdf_page_count(path: Path) -> int:
    import pymupdf

    with pymupdf.open(path) as document:
        return document.page_count


def split_pdf(source: Path, first_page: int, last_page: int, target: Path) -> None:
    """Copy pages first_page..last_page (1 = first page) of a PDF into a new PDF."""
    import pymupdf

    with pymupdf.open(source) as document:
        last_page = min(last_page, document.page_count)
        out = pymupdf.open()
        out.insert_pdf(document, from_page=first_page - 1, to_page=last_page - 1)
        target.parent.mkdir(parents=True, exist_ok=True)
        out.save(target, garbage=3, deflate=True)
        out.close()


def place_document(source: Path, kind: str, pages: tuple[int, int], total_pages: int, target: Path) -> Path:
    """
    Put (a copy of) an uploaded file at target. A page range of a PDF becomes its own PDF;
    a whole file is copied. The original in _originals is never changed.
    """
    target = unique_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    first, last = pages
    if kind == "pdf" and (first > 1 or last < total_pages):
        split_pdf(source, first, last, target)
    else:
        shutil.copy2(source, target)
    return target


def open_in_explorer(path: Path) -> bool:
    """Open a folder in Windows Explorer (or Finder / file manager). Only works on this PC."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except OSError:
        return False


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
