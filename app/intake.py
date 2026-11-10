"""
The drop box on the home screen: a pile of files from ONE case or MANY, sorted for you.

  1. start()       saves the files in workspace/_intake/<id> and reads them in the background
                   (text layer, OCR, Claude for hard pages - the same reader as a case's uploads).
  2. sort          ONE AI call sorts the files into cases - recognising cases you already have by CNR,
                   case number or parties - and makes each case's filing plan (document types, new names,
                   folders, hearing dates and deadlines with the sentence they come from).
                   Without AI: files are grouped by CNR / case number / "A v. B", with keyword plans.
  3. move_file()   you put a file into another case (or a new one): instant, no AI call.
  4. apply()       "Approve all": new cases are created, every file goes into its case's folders and the
                   dates into the calendar - by the same code as a case's own filing plan (organizer.apply_plan).

Nothing is created, moved or added to the calendar before step 4. Discarding moves the files to _deleted.
"""

from __future__ import annotations

import re
import threading
import traceback
from pathlib import Path

from app import db, files, llm, organizer, store
from app.bus import bus

_locks: dict[int, threading.Lock] = {}

# "FIR No. 345/2026", "Bail Application No. 1987 of 2026", "W.P.(C) No. 12/2025", "CC No. 55/2024" ...
NUMBER = re.compile(
    r"\b(FIR|Crime|Crl\.?\s?M\.?\s?C\.?|Crl\.?\s?A\.?|Crl\.?\s?Rev\.?|W\.?\s?P\.?\s?(?:\(\s?(?:C|Crl)\s?\))?|"
    r"C\.?\s?S\.?|O\.?\s?S\.?|O\.?\s?P\.?|C\.?\s?C\.?|C\.?\s?A\.?|M\.?\s?A\.?|E\.?\s?P\.?|S\.?\s?C\.?|"
    r"Bail\s+Appl(?:ication|n)?\.?|Complaint(?:\s+Case)?|Suit|Petition|Appeal|Case)"
    r"\s*No\.?\s*[:.]?\s*(\d{1,7})\s*(?:/|of|-)\s*((?:19|20)\d{2})\b", re.I)
CNR = re.compile(r"\b([A-Za-z]{4}\d{12})\b")
VERSUS = re.compile(r"^\s*(?:in re\s+)?([A-Z][\w .&'()/-]{1,70}?)\s+(?:v\.?|vs\.?|versus)\s+([A-Z][\w .&'()/-]{1,70}?)\s*[.,;:]?\s*$",
                    re.M | re.I)
HONORIFICS = re.compile(r"\b(?:the|m/s\.?|smt\.?|shri|sh\.|sri|mr\.?|mrs\.?|ms\.?|dr\.?|and (?:others|ors\.?|anr\.?)|"
                        r"& (?:ors\.?|anr\.?)|ors\.?|anr\.?)\b", re.I)


# ---------------------------------------------------------------------------
# 1. Receiving the files
# ---------------------------------------------------------------------------
def batch_name(intake_id: int) -> str:
    return f"intake{intake_id}"


def folder(intake_id: int) -> Path:
    path = files.resolve(f"_intake/{int(intake_id)}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def start(intake_id: int) -> None:
    organizer._jobs.submit(organizer._safe, _run, intake_id)


def publish(intake: dict | None) -> dict | None:
    if intake:
        bus.publish("intake", {"intake_id": intake["id"], "status": intake["status"], "progress": intake["progress"]})
    return intake


def _file_rows(uploads: list[dict]) -> list[dict]:
    return [{"id": u["id"], "name": u["original_name"], "status": u["status"], "pages": u["pages"],
             "error": u["error"]} for u in uploads]


def _run(intake_id: int) -> None:
    batch = batch_name(intake_id)
    try:
        def on_file(number: int, total: int, upload: dict) -> None:
            publish(store.update_intake(intake_id, progress=f"Reading file {number} of {total}: {upload['original_name']}"))

        uploads = organizer.read_batch(batch, on_file)
        ready = [u for u in uploads if u["status"] == "ready"]
        data = {"files": _file_rows(uploads), "groups": [], "notes": []}
        if not ready:
            publish(store.update_intake(intake_id, status="failed", data=data,
                                        progress="None of the files could be read. Try PDFs, Word files or clear photos."))
            return
        how = "with AI" if llm.available() else "by case numbers and party names"
        publish(store.update_intake(intake_id, status="sorting", data=data,
                                    progress=f"Sorting {len(ready)} file{'s' if len(ready) != 1 else ''} into cases {how}…"))
        data.update(sort(ready))
        failed = [u["original_name"] for u in uploads if u["status"] == "failed"]
        if failed:
            data["notes"].insert(0, "Couldn't read: " + ", ".join(failed) + ". They are left out.")
        cases = len(data["groups"])
        publish(store.update_intake(intake_id, status="ready", data=data,
                                    progress=f"{len(ready)} file{'s' if len(ready) != 1 else ''} sorted into "
                                             f"{cases} case{'s' if cases != 1 else ''}. Check, then approve."))
    except Exception as error:
        traceback.print_exc()
        publish(store.update_intake(intake_id, status="failed", progress=f"Something went wrong: {str(error)[:200]}"))


# ---------------------------------------------------------------------------
# 2. Sorting into cases (+ each case's filing plan)
# ---------------------------------------------------------------------------
def _parties_key(left: str, right: str) -> str:
    def clean(side: str) -> str:
        side = HONORIFICS.sub(" ", side.lower())
        words = re.findall(r"[a-z]+", side)
        return " ".join(words[:3])
    a, b = clean(left), clean(right)
    return f"parties:{a}|{b}" if a and b else ""


def clues(text: str) -> dict:
    """What ties a document to a case: CNR numbers, case numbers and 'A v. B' titles found in its text."""
    found: dict = {"keys": set(), "labels": {}, "title": ""}
    for match in CNR.finditer(text):
        cnr = store.normalise_cnr(match.group(1))
        if re.fullmatch(r"[A-Z]{4}\d{12}", cnr):
            found["keys"].add(f"cnr:{cnr}")
            found["labels"][f"cnr:{cnr}"] = f"CNR {cnr}"
    for match in NUMBER.finditer(text):
        kind = re.sub(r"[^a-z]", "", match.group(1).lower())
        kind = "bail" if kind.startswith("bail") else kind
        key = f"no:{kind}:{int(match.group(2))}/{match.group(3)}"
        found["keys"].add(key)
        name = re.sub(r"\s+", " ", match.group(1)).strip()
        found["labels"][key] = f"{name} No. {int(match.group(2))}/{match.group(3)}"
    for match in VERSUS.finditer(text[:6000]):
        key = _parties_key(match.group(1), match.group(2))
        if key:
            found["keys"].add(key)
            title = f"{match.group(1).strip()} v. {match.group(2).strip()}"
            found["labels"][key] = title
            found["title"] = found["title"] or title[:120]
    return found


def case_keys(case: dict) -> set[str]:
    text = "\n".join(str(case.get(k) or "") for k in ("case_number", "notes"))
    keys = clues(text)["keys"] | clues(re.sub(r"\s+(?:v|vs)\s+", " v. ", str(case.get("title") or "")))["keys"]
    if case.get("cnr"):
        keys.add(f"cnr:{store.normalise_cnr(case['cnr'])}")
    return keys


def _texts(uploads: list[dict], pages_by_upload: dict[int, list[dict]]) -> dict[int, str]:
    return {u["id"]: "\n".join(p["text"] for p in pages_by_upload.get(u["id"], [])) for u in uploads}


def rules_groups(uploads: list[dict], pages_by_upload: dict[int, list[dict]], cases: list[dict]) -> list[dict]:
    """Files that share a CNR, a case number or the same 'A v. B' parties belong together (also through a third
    file that mentions both numbers, e.g. an order citing the FIR and the bail application). Groups that share
    a key with one of your cases go into that case."""
    texts = _texts(uploads, pages_by_upload)
    info = {u["id"]: clues(texts[u["id"]]) for u in uploads}
    parent = {u["id"]: u["id"] for u in uploads}

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[str, int] = {}
    for upload in uploads:
        for key in info[upload["id"]]["keys"]:
            if key in owner:
                parent[root(upload["id"])] = root(owner[key])
            else:
                owner[key] = upload["id"]
    components: dict[int, list[int]] = {}
    for upload in uploads:
        components.setdefault(root(upload["id"]), []).append(upload["id"])

    known = {c["id"]: case_keys(c) for c in cases}
    by_id = {c["id"]: c for c in cases}
    groups, loose = [], []
    for ids in components.values():
        keys = set().union(*(info[i]["keys"] for i in ids))
        labels = {k: v for i in ids for k, v in info[i]["labels"].items()}
        if not keys:
            loose += ids
            continue
        # The strongest match: same CNR, then the same case number, then the same parties
        def score(case_id: int) -> int:
            shared = keys & known[case_id]
            return sum(3 if k.startswith("cnr:") else 2 if k.startswith("no:") else 1 for k in shared)
        best = max(known, key=score, default=None)
        case_id = best if best is not None and score(best) else None
        for i in ids:                       # the very same file is already filed in one of your cases
            twin = store.filed_upload_with_hash(next(u["sha256"] for u in uploads if u["id"] == i))
            if twin and case_id is None and twin["case_id"] in by_id:
                case_id = twin["case_id"]
        strongest = sorted(keys, key=lambda k: (not k.startswith("cnr:"), not k.startswith("no:"), k))[0]
        reason = "Same " + labels.get(strongest, strongest)
        title = by_id[case_id]["title"] if case_id else (next((info[i]["title"] for i in ids if info[i]["title"]), "")
                                                         or labels.get(strongest, "New case"))
        groups.append({"case_id": case_id, "title": title, "reason": reason, "upload_ids": sorted(ids)})
    if loose:
        names = {u["id"]: u["original_name"] for u in uploads}
        groups.append({"case_id": None, "title": "Files I couldn't place" if groups else Path(names[loose[0]]).stem,
                       "reason": "No CNR, case number or party names found in " + ("these files" if len(loose) > 1 else "this file")
                                 + " - move them to the right case if needed.",
                       "upload_ids": sorted(loose)})
    return groups


SORT_SCHEMA = {
    "type": "object",
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "existing_case_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                    "plan": organizer.PLAN_SCHEMA,
                },
                "required": ["existing_case_id", "title", "reason", "plan"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["cases", "notes"],
    "additionalProperties": False,
}

SORT_SYSTEM = """You are the senior filing clerk of an Indian law office. A lawyer has dropped a pile of files on
the desk. They may all belong to ONE case, or come from SEVERAL different cases.

A. Sort the files into cases. Files belong to the same case when they share a CNR, a case number (FIR No., bail
   application, suit / petition / complaint number - an order often cites several numbers of the same case), or
   the same parties and the same dispute. <clues> lists numbers and titles found in each file to help you.
   If a case is one of the lawyer's EXISTING cases (<existing_cases>), set existing_case_id to its id; otherwise 0.
   Link to an existing case only on clear evidence (same CNR or case number, or the same parties AND the same
   matter); when unsure, make a new case and say why in notes. Every file goes into exactly one case. A file
   that cannot be tied to any case gets a case of its own, explained in notes.
B. title: for a new case, the parties as "A v. B" (e.g. "State v. Ramesh Kumar"), or a short description if no
   parties are known ("Cheque bounce - Priya Traders"). For an existing case, its title as listed.
   reason: one short sentence on why these files go together (e.g. "Same FIR No. 345/2026, P.S. Saket").
C. For each case, make its filing plan ("plan") - only with that case's files - following these rules:
{rules}

notes (top level): warnings about the sorting itself (files you were unsure about, possible duplicates)."""


def _existing_cases_text(cases: list[dict]) -> str:
    lines = []
    for case in cases[:200]:
        parts = [case["title"], case.get("case_number") or "", f"CNR {case['cnr']}" if case.get("cnr") else "",
                 case.get("court") or "", f"client {case['client']}" if case.get("client") else "",
                 "closed" if case.get("status") == "closed" else ""]
        lines.append(f"id={case['id']} | " + " | ".join(p for p in parts if p))
    return "\n".join(lines) or "(no cases yet)"


def ai_sort(uploads: list[dict], pages_by_upload: dict[int, list[dict]], cases: list[dict]) -> dict:
    texts = _texts(uploads, pages_by_upload)
    hints = []
    for upload in uploads:
        found = clues(texts[upload["id"]])
        if found["labels"]:
            hints.append(f'<clues file="{upload["id"]}">' + "; ".join(sorted(set(found["labels"].values()))) + "</clues>")
    prompt = (f"<existing_cases>\n{_existing_cases_text(cases)}\n</existing_cases>\n\n" + "\n".join(hints) + "\n\n"
              + organizer.file_blocks(uploads, pages_by_upload, budget_total=120_000)
              + "\n\nSort these files into cases and make each case's filing plan.")
    system = SORT_SYSTEM.replace("{rules}", organizer.plan_rules_text())
    return llm.complete_json(system, prompt, SORT_SCHEMA, tier=llm.MAIN, timeout=600, max_tokens=32000)


def _empty_case(title: str) -> dict:
    return {"id": None, "title": title, **{f: "" for f in organizer.CASE_SUGGESTION_FIELDS if f != "title"}}


def _plan_for_group(group: dict, raw_plan: dict | None, uploads: dict[int, dict],
                    pages_by_upload: dict[int, list[dict]]) -> dict:
    """Normalise (or, without a raw plan, make by keywords) the filing plan of one group."""
    case = store.get_case(group["case_id"]) if group.get("case_id") else None
    case = case or _empty_case(group["title"])
    mine = [uploads[i] for i in group["upload_ids"]]
    if raw_plan is None:
        raw_plan = organizer.rules_plan(case, mine, pages_by_upload)
    ids = set(group["upload_ids"])
    raw_plan = {**raw_plan,
                "documents": [d for d in raw_plan.get("documents", []) if int(d.get("file_id") or 0) in ids],
                "events": [e for e in raw_plan.get("events", []) if int(e.get("file_id") or 0) in ids]}
    return organizer.normalise_plan(raw_plan, case, mine, pages_by_upload)


def _mark_duplicates(groups: list[dict], uploads: dict[int, dict]) -> None:
    """A file already filed in the same case, or dropped twice: left out (unticked), with a note."""
    seen: dict[str, str] = {}
    for group in groups:
        plan = group["plan"]
        for upload_id in group["upload_ids"]:
            upload = uploads[upload_id]
            twin = store.filed_upload_with_hash(upload["sha256"])
            again = seen.get(upload["sha256"])
            seen.setdefault(upload["sha256"], upload["original_name"])
            note = ""
            if twin and twin["case_id"] == group.get("case_id"):
                note = f"“{upload['original_name']}” is already filed in this case, so it is left out."
            elif again:
                note = f"“{upload['original_name']}” is the same file as “{again}”, so it is left out."
            if note:
                for doc in plan["documents"]:
                    if doc["upload_id"] == upload_id:
                        doc["include"] = False
                plan["notes"].insert(0, note)


def sort(uploads: list[dict]) -> dict:
    """Sort ready uploads into groups (one per case), each with its filing plan."""
    pages_by_upload = {u["id"]: store.upload_pages(u["id"]) for u in uploads}
    by_id = {u["id"]: u for u in uploads}
    cases = store.list_cases()
    valid = {c["id"]: c for c in cases}
    notes: list[str] = []
    made_by = "rules"
    raw_groups: list[tuple[dict, dict | None]] = []
    if llm.available():
        try:
            answer = ai_sort(uploads, pages_by_upload, cases)
            made_by = "ai"
            notes += [str(n).strip()[:300] for n in answer.get("notes", []) if str(n).strip()][:10]
            assigned: set[int] = set()
            merged: dict[int, tuple[dict, dict]] = {}       # two AI groups for the same existing case -> one
            for item in answer.get("cases", []):
                plan = item.get("plan") or {}
                ids = []
                for doc in plan.get("documents", []):
                    file_id = int(doc.get("file_id") or 0)
                    if file_id in by_id and file_id not in assigned and file_id not in ids:
                        ids.append(file_id)
                if not ids:
                    continue
                assigned.update(ids)
                case_id = int(item.get("existing_case_id") or 0)
                case_id = case_id if case_id in valid else None
                group = {"case_id": case_id, "title": valid[case_id]["title"] if case_id else
                         (str(item.get("title") or "").strip()[:160] or "New case"),
                         "reason": str(item.get("reason") or "").strip()[:300], "upload_ids": ids}
                if case_id and case_id in merged:
                    earlier, earlier_plan = merged[case_id]
                    earlier["upload_ids"] += ids
                    for part in ("documents", "events", "notes"):
                        earlier_plan[part] = list(earlier_plan.get(part, [])) + list(plan.get(part, []))
                    continue
                if case_id:
                    merged[case_id] = (group, plan)
                raw_groups.append((group, plan))
            leftover = [u for u in uploads if u["id"] not in assigned]
            if leftover:
                for group in rules_groups(leftover, pages_by_upload, cases):
                    raw_groups.append((group, None))
                notes.append("The AI did not place every file; the rest were sorted by case numbers and party names.")
        except Exception as error:
            traceback.print_exc()
            notes.insert(0, f"The AI could not sort the files ({str(error)[:160]}), so they were sorted by case numbers "
                            "and party names. Check the groups before approving.")
            raw_groups = []
            made_by = "rules"
    if not raw_groups:
        raw_groups = [(g, None) for g in rules_groups(uploads, pages_by_upload, cases)]
        if made_by == "rules" and not llm.available():
            notes.append("Sorted without AI (by CNR, case numbers and party names), with keyword filing plans. "
                         "Check the groups, types and dates before approving.")
    groups = []
    for number, (group, raw_plan) in enumerate(raw_groups, 1):
        group = {**group, "key": f"g{number}", "include": True,
                 "case_title": valid[group["case_id"]]["title"] if group.get("case_id") else ""}
        group["plan"] = _plan_for_group(group, raw_plan, by_id, pages_by_upload)
        group["made_by"] = "ai" if raw_plan is not None else "rules"
        groups.append(group)
    _mark_duplicates(groups, by_id)
    return {"groups": groups, "notes": notes, "made_by": made_by}


# ---------------------------------------------------------------------------
# 3. Moving a file to another case (instant: the plan entries move with it)
# ---------------------------------------------------------------------------
def _as_raw(plan: dict) -> dict:
    """A normalised plan back into the AI's shape, so normalise_plan() can check it again."""
    return {
        "summary": plan.get("summary", ""), "notes": list(plan.get("notes", [])),
        "documents": [{**d, "file_id": d["upload_id"], "filename": Path(d["filename"]).stem} for d in plan.get("documents", [])],
        "events": [{**e, "file_id": e.get("upload_id") or 0} for e in plan.get("events", [])],
        "case": {f: s.get("value", "") for f, s in (plan.get("case") or {}).items()},
    }


def _need(intake_id: int, statuses: tuple[str, ...] = ("ready",)) -> dict:
    intake = store.get_intake(intake_id)
    if not intake:
        raise LookupError("This upload was not found.")
    if intake["status"] not in statuses:
        raise ValueError("These files can't be changed any more." if intake["status"] != "sorting"
                         else "Still sorting the files - try again in a moment.")
    return intake


def _material(group_list: list[dict]) -> tuple[dict[int, dict], dict[int, list[dict]]]:
    ids = [i for g in group_list for i in g["upload_ids"]]
    uploads = {i: store.get_upload(i) for i in ids}
    return {i: u for i, u in uploads.items() if u}, {i: store.upload_pages(i) for i in ids}


def move_file(intake_id: int, upload_id: int, to_key: str) -> dict:
    with _locks.setdefault(intake_id, threading.Lock()):
        intake = _need(intake_id)
        data = intake["data"]
        groups = data.get("groups", [])
        source = next((g for g in groups if upload_id in g["upload_ids"]), None)
        if not source:
            raise ValueError("That file is not in this upload.")
        if to_key == source["key"]:
            return intake
        target = next((g for g in groups if g["key"] == to_key), None)
        if target is None:
            if to_key != "new":
                raise ValueError("That case is not in this upload.")
            number = max(int(g["key"][1:]) for g in groups) + 1
            name = Path(store.get_upload(upload_id)["original_name"]).stem
            target = {"key": f"g{number}", "case_id": None, "case_title": "", "title": name[:160], "include": True,
                      "reason": "Moved here by you.", "upload_ids": [], "made_by": "you",
                      "plan": {"summary": "", "documents": [], "events": [], "case": {}, "notes": [], "folders": []}}
            groups.append(target)
        moving_docs = [d for d in source["plan"]["documents"] if d["upload_id"] == upload_id]
        moving_events = [e for e in source["plan"]["events"] if (e.get("upload_id") or 0) == upload_id]
        source["upload_ids"] = [i for i in source["upload_ids"] if i != upload_id]
        source["plan"]["documents"] = [d for d in source["plan"]["documents"] if d["upload_id"] != upload_id]
        source["plan"]["events"] = [e for e in source["plan"]["events"] if (e.get("upload_id") or 0) != upload_id]
        target["upload_ids"].append(upload_id)
        target["plan"]["documents"] += moving_docs
        target["plan"]["events"] += moving_events
        data["groups"] = [g for g in groups if g["upload_ids"]]
        uploads, pages = _material(data["groups"])
        for group in (source, target):
            if group["upload_ids"]:
                case = (store.get_case(group["case_id"]) if group.get("case_id") else None) or _empty_case(group["title"])
                group["plan"] = organizer.normalise_plan(_as_raw(group["plan"]), case,
                                                         [uploads[i] for i in group["upload_ids"]], pages)
        return publish(store.update_intake(intake_id, data=data))


# ---------------------------------------------------------------------------
# 4. Approve all
# ---------------------------------------------------------------------------
def apply(intake_id: int, edits: dict | None = None) -> dict:
    """edits: {"groups": [{"key", "title", "include", "case_id" (0 = new case), "events": {event_key: bool}}]}"""
    lock = _locks.setdefault(intake_id, threading.Lock())
    with lock:
        intake = _need(intake_id)
        data = intake["data"]
        by_key = {g["key"]: g for g in data.get("groups", [])}
        for edit in (edits or {}).get("groups", []):
            group = by_key.get(str(edit.get("key")))
            if not group:
                continue
            if "title" in edit and str(edit["title"] or "").strip():
                group["title"] = str(edit["title"]).strip()[:160]
            if "include" in edit:
                group["include"] = bool(edit["include"])
            if "case_id" in edit:
                wanted = int(edit.get("case_id") or 0) or None
                if wanted and not store.get_case(wanted):
                    wanted = None
                group["case_id"] = wanted
            for event in group["plan"].get("events", []):
                if event["key"] in (edit.get("events") or {}):
                    event["include"] = bool(edit["events"][event["key"]])
        store.update_intake(intake_id, status="applying", progress="Filing the files…", data=data)
    publish(store.get_intake(intake_id))
    results = []
    for group in data.get("groups", []):
        if not group.get("include", True):
            _throw_away(group["upload_ids"], intake_id)
            continue
        try:
            results.append(_apply_group(intake_id, group))
        except Exception as error:
            traceback.print_exc()
            results.append({"key": group["key"], "title": group["title"], "error": str(error)[:200]})
    data["result"] = results
    done = [r for r in results if not r.get("error")]
    text = f"Done: {len(done)} case{'s' if len(done) != 1 else ''} updated."
    if len(done) < len(results):
        text += " Some files could not be filed - see below."
    intake = store.update_intake(intake_id, status="applied", progress=text, data=data)
    bus.publish("cases", {})
    bus.publish("calendar", {})
    return publish(intake)


def _apply_group(intake_id: int, group: dict) -> dict:
    plan = group["plan"]
    case = store.get_case(group["case_id"]) if group.get("case_id") else None
    is_new = case is None
    if is_new:
        details = {f: s.get("value", "") for f, s in (plan.get("case") or {}).items() if s.get("value")}
        case = store.create_case({**details, "title": group["title"] or details.get("title") or "New case"})
        plan = {**plan, "case": {}}                 # its details are set already
    chat = store.list_chats(case["id"])[0]
    batch = f"{batch_name(intake_id)}-{group['key']}"
    originals = files.originals_dir(case, None)
    uploads = []
    for upload_id in group["upload_ids"]:
        upload = store.get_upload(upload_id)
        if not upload:
            continue
        source = files.resolve(upload["path"])
        target = files.unique_path(originals / source.name)
        source.replace(target)
        db.run("UPDATE uploads SET case_id = ?, chat_id = ?, batch = ?, path = ? WHERE id = ?",
               (case["id"], chat["id"], batch, files.relative(target), upload_id))
        uploads.append(store.get_upload(upload_id))
    if not is_new:
        # Check the plan against this case as it is now: only empty case details get filled in, and a date
        # that is already in its calendar is not added twice. Your ticks are kept.
        pages = {u["id"]: store.upload_pages(u["id"]) for u in uploads}
        plan = organizer.normalise_plan(_as_raw(plan), case, uploads, pages)
        for event in plan["events"]:
            if event.get("exists"):
                event["include"] = False
    plan_row = store.create_plan(case["id"], chat["id"], batch, status="draft")
    store.update_plan(plan_row["id"], data=plan, made_by=group.get("made_by") or "ai")
    organizer.post(chat["id"], "notice", "These files came from the drop box on the home screen and were sorted into "
                                         "this case.")
    bus.publish("cases", {"case_id": case["id"]})
    title = store.get_case(case["id"])["title"]
    try:
        applied = organizer.apply_plan(plan_row["id"])
    except Exception as error:
        # Keep the plan: it waits in the case chat, where it can be fixed and approved again
        traceback.print_exc()
        organizer.post(chat["id"], "plan", "", {"plan_id": plan_row["id"], "status": "draft"})
        reason = "the folder path is too long for Windows" if len(str(getattr(error, "filename", "") or "")) > 240 \
            else str(error)[:160]
        return {"key": group["key"], "case_id": case["id"], "title": title, "new": is_new,
                "error": f"Couldn't file the documents ({reason}). The case was made and its filing plan is waiting "
                         "in the case - open it to try again."}
    result = applied["plan"]["data"].get("result", {})
    return {"key": group["key"], "case_id": case["id"], "title": title, "new": is_new,
            "documents": len(result.get("documents", [])), "events": len(result.get("events", []))}


def _throw_away(upload_ids: list[int], intake_id: int) -> None:
    """Files you left out: off the list, and moved to workspace/_deleted (not erased)."""
    bin_dir = files.resolve(f"_deleted/intake-{intake_id}")
    for upload_id in upload_ids:
        upload = store.get_upload(upload_id)
        if not upload:
            continue
        source = files.resolve(upload["path"])
        if source.exists():
            bin_dir.mkdir(parents=True, exist_ok=True)
            source.replace(files.unique_path(bin_dir / source.name))
        db.run("DELETE FROM uploads WHERE id = ?", (upload_id,))


def discard(intake_id: int) -> dict:
    with _locks.setdefault(intake_id, threading.Lock()):
        intake = _need(intake_id, ("ready", "failed"))
        _throw_away([u["id"] for u in store.batch_uploads(batch_name(intake_id))], intake_id)
        return publish(store.update_intake(intake_id, status="discarded", progress="Discarded. Nothing was filed."))


def close(intake_id: int) -> dict:
    """Hide a finished upload's summary from the home screen."""
    intake = _need(intake_id, ("applied",))
    return publish(store.update_intake(intake_id, status="closed"))
