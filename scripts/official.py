"""
Download official PDFs: India Code (indiacode.gov.in, the Government's legislation site) and court websites.

India Code moved to DSpace in 2026; its public REST API lets us find an Act or a set of Rules by title
and download the PDF published by the Legislative Department. Files are cached in data/raw/official/,
so each one is downloaded only once.

    from scripts.official import fetch
    path = fetch("GENCLAUSES", indiacode="The General Clauses Act, 1897")
    path = fetch("SCR2013", url="https://.../supreme-court-rules-2013.pdf")
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

PROJECT = Path(__file__).resolve().parent.parent
RAW = PROJECT / "data" / "raw" / "official"
API = "https://indiacode.gov.in/server/api"
HEADERS = {"User-Agent": "SamvidhanAI-library-builder/1.0 (+local legal research tool)"}
_client: httpx.Client | None = None


def _tls_context():
    """Check certificates with Windows' own certificate store (like Edge / Chrome do). Many court sites
    send an incomplete certificate chain; Windows fills in the missing piece, Python's bundle cannot.
    Verification stays fully on."""
    try:
        import ssl

        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return True


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=180, follow_redirects=True, headers=HEADERS, verify=_tls_context())
    return _client


def _get(url: str, **kwargs) -> httpx.Response:
    """GET with a few polite retries (government sites are sometimes slow)."""
    last = None
    for attempt in range(4):
        try:
            response = client().get(url, **kwargs)
            if response.status_code < 500:
                return response
            last = RuntimeError(f"HTTP {response.status_code} for {url}")
        except httpx.HTTPError as error:
            last = error
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Could not download {url}: {last}")


def _plain(text: str) -> str:
    text = re.sub(r"&amp;", "&", text or "")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", re.sub(r"^the ", "", text.strip())).strip()


CENTRAL_ACTS = "69a0c1fb-7b22-4481-b16a-1dc59b5d02e6"     # India Code collection "Acts" (Central Acts)


def browse_central(title: str) -> list[dict]:
    """Central Acts whose title starts like `title` (India Code's browse-by-title; exact and reliable)."""
    start = re.sub(r",?\s*\d{4}\s*$", "", title).strip()
    browse_from = re.split(r"['’]", start)[0].strip()       # India Code uses curly apostrophes
    response = _get(f"{API}/discover/browses/title/items", params={"scope": CENTRAL_ACTS, "startsWith": browse_from, "size": 8},
                    headers={"Accept": "application/json"})
    if response.status_code != 200:
        return []
    wanted = _plain(title)
    items = [{"uuid": it["uuid"], "name": it.get("name"), "metadata": it.get("metadata", {})}
             for it in response.json().get("_embedded", {}).get("items", [])]

    def year(item: dict) -> int:
        found = re.findall(r"\b(1[89]\d\d|20\d\d)\b", item["name"] or "")
        return int(found[-1]) if found else 0

    # exact title first; with no year in the request, the newest Act of that name (the one in force)
    return sorted((it for it in items if _plain(it["name"]).startswith(_plain(start))),
                  key=lambda it: (_plain(it["name"]) != wanted, -year(it)))


def find_items(title: str, state: str = "CENTRAL", uuid: str | None = None) -> list[dict]:
    """India Code items matching an Act / Rules title, best match first: [{uuid, name, metadata}]."""
    if uuid:
        item = _get(f"{API}/core/items/{uuid}", headers={"Accept": "application/json"}).json()
        return [{"uuid": uuid, "name": item.get("name"), "metadata": item.get("metadata", {})}]
    if state.upper() == "CENTRAL" and "rules" not in title.lower():
        found = browse_central(title)
        if found:
            return found
    response = _get(f"{API}/discover/search/objects", params={"query": f'"{title}"', "dsoType": "ITEM", "size": 40},
                    headers={"Accept": "application/json"})
    objects = response.json()["_embedded"]["searchResult"]["_embedded"]["objects"]
    wanted = _plain(title)
    ranked: list[tuple[int, dict]] = []
    for obj in objects:
        item = obj["_embedded"]["indexableObject"]
        md = item.get("metadata", {})
        if "dc.identifier.section_id" in md:          # one section of an Act, not the whole Act
            continue
        name = _plain(item.get("name") or "")
        item_state = "; ".join(v["value"] for v in md.get("dc.identifier.state_name", []))
        score = 0
        if name == wanted:
            score += 10
        elif name.startswith(wanted) or wanted.startswith(name):
            score += 5
        elif wanted in name:
            score += 2
        else:
            continue
        if state and item_state.upper() == state.upper():
            score += 3
        if "dc.identifier.rule_id" not in md and "rules" not in wanted:
            score += 1                                # prefer the Act itself over rules made under it
        ranked.append((score, {"uuid": item["uuid"], "name": item.get("name"), "metadata": md}))
    if not ranked:
        raise RuntimeError(f"India Code has no item titled {title!r}")
    ranked.sort(key=lambda pair: -pair[0])
    return [item for _, item in ranked]


def item_pdfs(uuid: str) -> list[dict]:
    """The PDFs (ORIGINAL bundle) of an India Code item: [{name, size, url}]."""
    bundles = _get(f"{API}/core/items/{uuid}/bundles", headers={"Accept": "application/json"}).json()
    found = []
    for bundle in bundles.get("_embedded", {}).get("bundles", []):
        if bundle["name"] != "ORIGINAL":
            continue
        streams = _get(bundle["_links"]["bitstreams"]["href"], headers={"Accept": "application/json"}).json()
        for stream in streams.get("_embedded", {}).get("bitstreams", []):
            if stream["name"].lower().endswith(".pdf"):
                found.append({"name": stream["name"], "size": stream.get("sizeBytes") or 0,
                              "url": stream["_links"]["content"]["href"]})
    return found


def pdf_link_on_page(page_url: str, pattern: str | None = None) -> str:
    """The first PDF linked from a web page (e.g. a court's page for one set of rules)."""
    from urllib.parse import urljoin

    response = _get(page_url)
    # a link, or a PDF embedded in a viewer (src= / data=)
    for href in re.findall(r'(?:href|src|data|file)=["\']([^"\']+?\.pdf[^"\']*)["\']', response.text, re.I):
        if not pattern or re.search(pattern, href, re.I):
            return urljoin(str(response.url), href)
    raise RuntimeError(f"no PDF link on {page_url}")


KERALA_HC = "https://highcourt.kerala.gov.in/"


def kerala_document(file_name: str) -> bytes:
    """A document from the Kerala High Court's Acts/Rules page. The site serves files through a small form
    (download_file.php) that every visitor's browser submits with a token from the page - done the same way here."""
    last = None
    for attempt in range(4):              # the server sometimes drops a connection: wait and try again
        try:
            home = _get(KERALA_HC).text
            token = re.search(r"""name=["']cssr["']\s+value=["']([^"']+)""", home)
            if not token:
                raise RuntimeError("the Kerala High Court page did not load as expected")
            response = client().post(KERALA_HC + "download_file.php", data={"cssr": token.group(1), "file_name": file_name},
                                     headers={"Referer": KERALA_HC, "Origin": KERALA_HC.rstrip("/")})
            if response.status_code == 200 and response.content.startswith(b"%PDF"):
                return response.content
            last = RuntimeError(f"Kerala High Court: {file_name} did not download (HTTP {response.status_code})")
        except (httpx.HTTPError, RuntimeError) as error:
            last = error
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(str(last))


def fetch_parts(code: str, parts: list[dict]) -> tuple[Path, list[dict]]:
    """Download several PDFs (chapters of one book) and join them into <code>.pdf.
    parts: [{"url", "label"}]. Returns (joined path, parts with "file", "first_page", "pages")."""
    import pymupdf

    RAW.mkdir(parents=True, exist_ok=True)
    folder = RAW / code
    folder.mkdir(exist_ok=True)
    joined = pymupdf.open()
    done = []
    for i, part in enumerate(parts, 1):
        path = folder / f"part{i:03d}.pdf"
        if not (path.exists() and path.stat().st_size > 500):
            if part.get("kerala_file"):
                path.write_bytes(kerala_document(part["kerala_file"]))
            else:
                response = _get(part["url"])
                if response.status_code != 200 or not response.content.startswith(b"%PDF"):
                    raise RuntimeError(f"{code} part {i}: not a PDF (HTTP {response.status_code}) from {part['url']}")
                path.write_bytes(response.content)
            time.sleep(0.3)
        with pymupdf.open(str(path)) as doc:
            first = joined.page_count + 1
            joined.insert_pdf(doc)
            done.append({**part, "file": str(path), "first_page": first, "pages": doc.page_count})
    out = RAW / f"{code}.pdf"
    joined.save(str(out), garbage=3, deflate=True)
    (RAW / f"{code}.json").write_text(json.dumps({"code": code, "url": parts[0].get("url", KERALA_HC) if parts else "", "parts": done,
                                                  "downloaded": time.strftime("%Y-%m-%d")}, indent=1, ensure_ascii=False),
                                      encoding="utf-8")
    return out, done


def fetch(code: str, indiacode: str | None = None, url: str | None = None, state: str = "CENTRAL",
          uuid: str | None = None, pick: str | None = None, page_url: str | None = None,
          kerala_file: str | None = None) -> Path:
    """Download (once) the official PDF for `code`. Returns the local path; writes <code>.json with where it came from."""
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{code}.pdf"
    meta_path = RAW / f"{code}.json"
    if path.exists() and path.stat().st_size > 1000:
        return path
    info: dict = {"code": code}
    if kerala_file:
        content = kerala_document(kerala_file)
        path.write_bytes(content)
        info.update({"url": KERALA_HC, "page": KERALA_HC, "file": kerala_file, "bytes": len(content),
                     "downloaded": time.strftime("%Y-%m-%d")})
        meta_path.write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
        return path
    if page_url and not url:
        url = pdf_link_on_page(page_url)
        info["page"] = page_url
    if url:
        info["url"] = url
        size = _remote_size(url)
        if size > MAX_BYTES:
            raise RuntimeError(f"the PDF is {size // 1_000_000} MB (a large scan) - skipped; add it later from the "
                               f"Library page or raise MAX_BYTES in scripts/official.py")
    else:
        item, pdfs = None, []
        for candidate in find_items(indiacode or code, state=state, uuid=uuid)[:6]:
            pdfs = item_pdfs(candidate["uuid"])
            if pdfs:
                item = candidate
                break
        if not item:
            raise RuntimeError(f"India Code has no PDF for {indiacode or code!r}")
        if pick:
            pdfs = [p for p in pdfs if re.search(pick, p["name"], re.I)] or pdfs
        # The English Act PDF is usually named like A1897-10.pdf; prefer that, then the largest file
        # (Hindi texts are named like h202211.pdf)
        pdfs.sort(key=lambda p: (bool(re.match(r"(?i)h\d{4,}", p["name"])), not re.match(r"(?i)a\d{4}-\d+_?\.pdf$", p["name"]),
                                 -p["size"]))
        chosen, response = pdfs[0], None
        if len(pdfs) > 1:                    # some items also carry the Hindi text: take the English one
            for candidate in pdfs[:4]:
                attempt = _get(candidate["url"])
                if attempt.status_code == 200 and attempt.content.startswith(b"%PDF") and _english(attempt.content):
                    chosen, response = candidate, attempt
                    break
        info.update({"url": chosen["url"], "indiacode_uuid": item["uuid"], "indiacode_title": item["name"],
                     "file": chosen["name"], "page": f"https://indiacode.gov.in/items/{item['uuid']}"})
        if response is not None:
            path.write_bytes(response.content)
            info.update({"bytes": len(response.content), "downloaded": time.strftime("%Y-%m-%d")})
            meta_path.write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
            return path
    response = _get(info["url"])
    if response.status_code != 200 or not response.content.startswith(b"%PDF"):
        raise RuntimeError(f"{code}: not a PDF (HTTP {response.status_code}) from {info['url']}")
    path.write_bytes(response.content)
    info["bytes"] = len(response.content)
    info["downloaded"] = time.strftime("%Y-%m-%d")
    meta_path.write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.5)                                   # be gentle with the server
    return path


MAX_BYTES = 70_000_000      # bigger files are huge scans: hours of OCR on a laptop


def _remote_size(url: str) -> int:
    try:
        with client().stream("GET", url) as response:
            return int(response.headers.get("content-length") or 0)
    except (httpx.HTTPError, ValueError):
        return 0


def _english(content: bytes) -> bool:
    """True when the first pages of a PDF are mostly English text (not Hindi, not a bare scan)."""
    import pymupdf

    try:
        with pymupdf.open(stream=content, filetype="pdf") as doc:
            text = "".join(doc[i].get_text() for i in range(min(4, doc.page_count)))
    except Exception:
        return False
    latin = len(re.findall(r"[A-Za-z]", text))
    devanagari = len(re.findall(r"[ऀ-ॿ]", text))
    return latin > 400 and latin > devanagari * 3


def source_info(code: str) -> dict:
    meta_path = RAW / f"{code}.json"
    return json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
