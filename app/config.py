"""
All settings in one place.

Secrets (your API key) and personal choices (which model) come from the
".env" file, so they never have to be written into the code.
"""

from __future__ import annotations

import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# ---- Folders ---------------------------------------------------------------
APP_NAME = "Samvidhan AI"
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_DIR / "data" / "constitution.json"  # the Constitution text
CASES_FILE = PROJECT_DIR / "data" / "cases.json"        # notes on landmark Supreme Court judgments
LAWS_DIR = PROJECT_DIR / "data" / "laws"                # the Acts library (scripts/build_laws.py)
SKILLS_DIR = PROJECT_DIR / "skills"                     # workflow "Skill" files (plain English, editable)
INDEX_DIR = PROJECT_DIR / "index"                       # ChromaDB vector index
MODEL_CACHE_DIR = PROJECT_DIR / ".cache" / "models"     # embedding model download
STATIC_DIR = Path(__file__).resolve().parent / "static" # the web page files

# Read the .env file (if it exists) into environment variables.
load_dotenv(PROJECT_DIR / ".env")

# Your own work - cases, files, chats, calendar - lives here (never shared).
# Tests point this at a temporary folder.
WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", "").strip() or (PROJECT_DIR / "workspace"))
MAX_UPLOAD_MB = 60          # largest single file you can upload
MAX_FILES_PER_UPLOAD = 40

# ---- Who writes the answers --------------------------------------------------
# auto        = API key if you added one, otherwise Claude Code on this PC, otherwise search-only
# api         = only the Claude API (needs ANTHROPIC_API_KEY)
# claude_code = only Claude Code (uses the Claude subscription you're signed in with in VS Code)
# off         = search-only, no AI answers
ANSWER_ENGINE = os.getenv("ANSWER_ENGINE", "").strip().lower() or "auto"
CLAUDE_CODE_PATH = os.getenv("CLAUDE_CODE_PATH", "").strip()    # optional: where claude.exe is
CLAUDE_CODE_MODEL = os.getenv("CLAUDE_CODE_MODEL", "").strip()  # optional: empty = Claude Code's default

# ---- Claude API ------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "").strip() or "claude-opus-5-5"
FAST_MODEL = "claude-haiku-5-5"  # small jobs, like turning a Hindi question into English search words
# The same two tiers when Claude Code answers (its own short model names)
CLAUDE_CODE_FAST_MODEL = os.getenv("CLAUDE_CODE_FAST_MODEL", "").strip() or "haiku"
CLAUDE_CODE_PARALLEL = 3  # how many Claude Code helpers may run at once (review tables, OCR)
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "").strip().lower() or "medium"
if CLAUDE_EFFORT not in {"low", "medium", "high", "xhigh", "max"}:
    CLAUDE_EFFORT = "medium"

# A safety cap on how long one answer (including Claude's thinking) can be.
# Normal answers are far shorter; this only limits cost if something goes wrong.
MAX_OUTPUT_TOKENS = 16_000

# Price in US dollars per 1 million tokens: (input, output).
# Used to show you what each answer cost. Source: Anthropic pricing, Oct 2026.
PRICES_PER_MILLION = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
}
try:
    USD_TO_INR = float(os.getenv("USD_TO_INR", "88"))
except ValueError:
    USD_TO_INR = 88.0

# ---- Search ----------------------------------------------------------------
TOP_K = 6                  # how many Articles / Schedule entries Claude gets as sources
MAX_CASES = 3              # plus up to this many landmark-judgment notes
SEARCH_RESULTS = 8         # how many Articles the search box shows
MAX_QUESTION_CHARS = 2000  # longer questions are rejected (protects your credits)
MAX_SOURCE_CHARS = 6000    # very long Articles: only the matching clauses are sent


@lru_cache(maxsize=1)
def find_claude_code() -> str | None:
    """
    Find the Claude Code program (claude.exe).

    It comes with the Claude Code extension for VS Code, so we look in the VS Code
    extensions folder (newest version first), then the usual install places.
    """
    if CLAUDE_CODE_PATH and Path(CLAUDE_CODE_PATH).exists():
        return CLAUDE_CODE_PATH
    on_path = shutil.which("claude")
    if on_path:
        return on_path

    def version(path: Path) -> tuple[int, ...]:
        match = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", str(path))
        return tuple(int(n) for n in match.groups()) if match else (0,)

    home = Path.home()
    candidates = [home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude"]
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        folder = home / editor / "extensions"
        if folder.exists():
            candidates += sorted(
                folder.glob("anthropic.claude-code-*/resources/native-binary/claude*"), key=version, reverse=True
            )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def answer_engine() -> str:
    """Which engine writes answers right now: 'api', 'claude_code' or 'off'."""
    if ANSWER_ENGINE == "off":
        return "off"
    if ANSWER_ENGINE in ("api", "auto") and ANTHROPIC_API_KEY:
        return "api"
    if ANSWER_ENGINE in ("claude_code", "auto") and find_claude_code():
        return "claude_code"
    return "off"


def engine_label() -> str:
    """A short description of the engine, shown on the page."""
    engine = answer_engine()
    if engine == "api":
        return f"{CLAUDE_MODEL} via API key"
    if engine == "claude_code":
        return "Claude Code (your Claude subscription)"
    return "Search-only"


def ai_enabled() -> bool:
    """AI answers are on when an API key is set or Claude Code is installed on this PC."""
    return answer_engine() != "off"
