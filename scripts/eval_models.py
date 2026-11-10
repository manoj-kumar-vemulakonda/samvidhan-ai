"""
Which Claude model is good enough for each job? A small test (an "eval") with known right answers.

    .venv\\Scripts\\python.exe scripts\\eval_models.py                    Claude Code: haiku, sonnet, opus
    .venv\\Scripts\\python.exe scripts\\eval_models.py --models sonnet,opus:high
    .venv\\Scripts\\python.exe scripts\\eval_models.py --jobs sort         only the drop box
    .venv\\Scripts\\python.exe scripts\\eval_models.py --set private       your own real files (evals\\private)
    .venv\\Scripts\\python.exe scripts\\eval_models.py --engine api --models claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5

"opus:high" means the model with more thinking (effort high), like the Deep button.

Two jobs are tested, through the app's own code:
  sort    The home-screen drop box. A pile of files goes in; the test checks which files were put together,
          what each document was called, and the hearing dates found. "no AI" (the keyword rules) is
          always tested too, for comparison. Right answers: evals/<set>/piles/.
  answer  Questions on Indian law. The answer must cite the right provisions, state the key facts, and
          cite nothing that doesn't exist. Right answers: evals/<set>/questions.json.

Every model gets the same files, the same questions and the same law search, so only the model differs.
The test uses its own empty workspace (.cache\\eval-ws): your cases are never touched. The law search runs
without the meaning index (keywords and exact references), the same for every model.

Cost: with Claude Code the test uses your subscription; the cost shown is what the same calls would cost
on the API. With --engine api it is charged to your API credits.

The report is printed and saved in evals\\results\\ (kept out of GitHub, like evals\\private\\).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
EVALS = PROJECT / "evals"
EVAL_WORKSPACE = PROJECT / ".cache" / "eval-ws"
DEFAULT_MODELS = {"claude_code": "haiku,sonnet,opus", "api": "claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5"}
NO_AI = "no AI (rules)"
GOOD_ENOUGH = 0.05  # a model within 5 points of the best one counts as good enough


# ---------------------------------------------------------------------------
# Dates in the test files: {d+4} = 4 days after today, {d-21} = 21 days before.
# {d+4:long} is written out, e.g. "14th November 2026"; {d+4:slash} is 14/11/2026.
# ---------------------------------------------------------------------------
RELATIVE_DAY = re.compile(r"\{d([+-]\d+)(?::(long|slash))?\}")


def _ordinal(day: int) -> str:
    return f"{day}{'th' if 11 <= day % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')}"


def render(text: str, today: date, iso: bool = False) -> str:
    """Put real dates into a test text: dd.mm.yyyy as in Indian court papers (or yyyy-mm-dd for the answers)."""
    def day(match: re.Match) -> str:
        when = today + timedelta(days=int(match.group(1)))
        if iso:
            return when.isoformat()
        if match.group(2) == "long":
            return f"{_ordinal(when.day)} {when:%B %Y}"
        if match.group(2) == "slash":
            return when.strftime("%d/%m/%Y")
        return when.strftime("%d.%m.%Y")
    return RELATIVE_DAY.sub(day, text)


# ---------------------------------------------------------------------------
# Scoring (plain functions, tested in tests/test_eval_models.py)
# ---------------------------------------------------------------------------
def score_sort(result: dict, names: dict[int, str], expected: dict, today: date) -> dict:
    """
    How well one pile was sorted, from 0 to 1 on three things:
      grouping  every pair of files: together when they should be, apart when they shouldn't
      types     each file named as the right kind of document
      dates     each expected hearing found (right date, and time if given); a coming hearing that
                isn't in the file counts as a mistake
    """
    mistakes: list[str] = []

    # 1. Grouping. Files the rules couldn't place are not "together" with each other.
    got: dict[str, str] = {}
    for number, group in enumerate(result.get("groups", [])):
        loose = group.get("made_by") == "rules" and str(group.get("reason", "")).startswith("No CNR")
        for upload_id in group.get("upload_ids", []):
            got[names[upload_id]] = f"{number}:{upload_id}" if loose else str(number)
    want = {name: index for index, group in enumerate(expected["groups"]) for name in group}
    files = sorted(want)
    pairs = [(a, b) for i, a in enumerate(files) for b in files[i + 1:]]
    right = 0
    for a, b in pairs:
        together = want[a] == want[b]
        if together == (a in got and got.get(a) == got.get(b)):
            right += 1
        else:
            mistakes.append(f"{a} + {b} should be {'in the same case' if together else 'in different cases'}")
    grouping = right / len(pairs) if pairs else 1.0

    # 2. Document types
    described: dict[str, list[str]] = {}
    for group in result.get("groups", []):
        for document in group.get("plan", {}).get("documents", []):
            name = names.get(document.get("upload_id"))
            if name:
                described.setdefault(name, []).append(f"{document.get('doc_type', '')} | {document.get('title', '')}".lower())
    types_right = 0
    for name, accepted in expected.get("types", {}).items():
        if any(word.lower() in text for text in described.get(name, []) for word in accepted):
            types_right += 1
        else:
            mistakes.append(f"{name} called {described.get(name) or 'nothing'}; expected {' / '.join(accepted)}")
    types = types_right / len(expected["types"]) if expected.get("types") else 1.0

    # 3. Dates
    events = [e for group in result.get("groups", []) for e in group.get("plan", {}).get("events", [])]
    wanted = [(render(d["date"], today, iso=True), d.get("time", ""), d.get("what", "")) for d in expected.get("dates", [])]
    found = 0
    for when, at, what in wanted:
        if any(e.get("date") == when and (not at or e.get("time") == at) for e in events):
            found += 1
        else:
            mistakes.append(f"missed the date {when}{' ' + at if at else ''} ({what})")
    expected_days = {when for when, _, _ in wanted}
    extra = [e for e in events if e.get("include") and e.get("kind") == "hearing" and e.get("date", "") >= today.isoformat()
             and e.get("date") not in expected_days]
    for e in extra:
        mistakes.append(f"made up a hearing on {e.get('date')}: {e.get('title', '')}")
    dates = found / (len(wanted) + len(extra)) if (wanted or extra) else 1.0

    return {"grouping": grouping, "types": types, "dates": dates, "score": (grouping + types + dates) / 3,
            "mistakes": mistakes, "made_by": result.get("made_by", "")}


def score_answer(text: str, citations: list[dict], item: dict) -> dict:
    """
    One answer, from 0 to 1: one point for each provision it had to cite (green or amber chip), each key fact
    it had to state, each wrong statement it avoided, and one for citing nothing that doesn't exist (red chip).
    """
    lower = re.sub(r"[*_`]", "", text or "").lower()  # "**cognizable**" reads as "cognizable"
    cited = {c.get("id") for c in citations if c.get("status") in ("verified", "library")}
    made_up = [c.get("text", "") for c in citations if c.get("status") in ("unknown", "outside")]
    checks, mistakes = 0, []
    passed = 0
    for options in item.get("cite", []):
        checks += 1
        if cited & set(options):
            passed += 1
        else:
            mistakes.append(f"did not cite {' or '.join(options)}")
    for options in item.get("mention", []):
        checks += 1
        if any(option.lower() in lower for option in options):
            passed += 1
        else:
            mistakes.append(f"did not say {' / '.join(options)}")
    for wrong in item.get("avoid", []):
        checks += 1
        if wrong.lower() in lower:
            mistakes.append(f"said '{wrong}'")
        else:
            passed += 1
    checks += 1
    if made_up:
        mistakes.append(f"red citations: {', '.join(made_up)}")
    else:
        passed += 1
    return {"score": passed / checks, "cites_ok": sum(1 for o in item.get("cite", []) if cited & set(o)),
            "cites": len(item.get("cite", [])), "made_up": len(made_up), "mistakes": mistakes}


def good_enough(rows: list[dict]) -> dict | None:
    """The cheapest model whose score is within 5 points of the best AI model."""
    ai_rows = [r for r in rows if r["model"] != NO_AI and r["items"]]
    if not ai_rows:
        return None
    best = max(r["score"] for r in ai_rows)
    close = [r for r in ai_rows if r["score"] >= best - GOOD_ENOUGH]
    return min(close, key=lambda r: (r["usd"], -r["score"]))


# ---------------------------------------------------------------------------
# Loading the test set
# ---------------------------------------------------------------------------
def load_piles(folder: Path, today: date) -> list[dict]:
    """Sample piles are .json files (texts inside); your real piles are folders of files + expected.json."""
    piles = []
    for path in sorted((folder / "piles").glob("*")) if (folder / "piles").exists() else []:
        if path.suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            texts = {name: render(text, today) for name, text in data["files"].items()}
            piles.append({"name": path.stem, "title": data.get("title", path.stem), "texts": texts, "expected": data["expected"]})
        elif path.is_dir() and (path / "expected.json").exists():
            data = json.loads((path / "expected.json").read_text(encoding="utf-8"))
            real = [f for f in sorted(path.iterdir()) if f.is_file() and f.name != "expected.json"]
            piles.append({"name": path.name, "title": data.get("title", path.name), "paths": real, "expected": data["expected"]})
    return piles


def uploads_for(pile: dict, number: int) -> list[dict]:
    """Put a pile into the test workspace as uploads, read and ready to sort (done once per pile)."""
    from app import config, files, organizer, store

    batch = f"eval-{number}"
    if "texts" in pile:
        out = []
        for name, text in pile["texts"].items():
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            upload = store.add_upload(None, None, batch, name, f"_intake/{batch}/{name}", len(text), digest, "pdf")
            pages = [(i, part, "text") for i, part in enumerate(text.split("\f"), 1)]
            store.set_upload_pages(upload["id"], pages)
            out.append(store.update_upload(upload["id"], status="ready", pages=len(pages)))
        return out
    folder = config.WORKSPACE_DIR / "_intake" / batch
    for path in pile["paths"]:
        kind = files.file_kind(path.name)
        if not kind:
            print(f"   skipped {path.name}: this kind of file can't be read")
            continue
        with path.open("rb") as source:
            saved, size, digest = files.save_upload(source, folder, path.name, config.MAX_UPLOAD_MB * 1024 * 1024)
        store.add_upload(None, None, batch, path.name, files.relative(saved), size, digest, kind)
    print(f"   reading {len(pile['paths'])} files (scans are read with OCR, a few seconds a page)...")
    uploads = organizer.read_batch(batch)
    for upload in uploads:
        if upload["status"] != "ready":
            print(f"   could not read {upload['original_name']}: {upload.get('error') or upload['status']}")
    return [u for u in uploads if u["status"] == "ready"]


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
class Meter:
    """Adds up the API price of every AI call (Claude Code reports it too: what the API would charge),
    and notes which model really answered."""

    def __init__(self) -> None:
        self.usd = 0.0
        self.models: set[str] = set()

    def reset(self) -> None:
        self.usd, self.models = 0.0, set()

    def install(self) -> None:
        from app import answer, llm

        run_claude_code, make_client = llm._cc_run, llm._api_client

        def counted_run(command, stdin_text, timeout):
            result = run_claude_code(command, stdin_text, timeout)
            self.usd += float(result.get("total_cost_usd") or 0)
            self.models.update((result.get("modelUsage") or {}).keys())
            return result

        def counted_client():
            client = make_client()

            def create(**params):
                response = client.messages.create(**params)
                model = getattr(response, "model", "") or params["model"]
                self.usd += answer.estimate_cost(model, response.usage)["usd"]
                self.models.add(model)
                return response
            return SimpleNamespace(messages=SimpleNamespace(create=create))

        llm._cc_run, llm._api_client = counted_run, counted_client


def use_model(engine: str, spec: str, effort: str) -> None:
    """Point the app's everyday ('main') model at `spec` - 'sonnet', 'opus:high', a model id, or no AI."""
    from app import config

    if spec == NO_AI:
        config.ANSWER_ENGINE = "off"
        return
    name, _, level = spec.partition(":")
    config.ANSWER_ENGINE = engine
    config.CLAUDE_EFFORT = level or effort
    if engine == "api":
        config.CLAUDE_MODEL = name
    else:
        config.CLAUDE_CODE_MODEL = name


def run_sort(piles: list[dict], models: list[str], engine: str, effort: str, meter: Meter, today: date) -> list[dict]:
    from app import intake

    prepared = []
    for number, pile in enumerate(piles, 1):
        print(f"Pile {number}: {pile['title']}")
        uploads = uploads_for(pile, number)
        prepared.append((pile, uploads, {u["id"]: u["original_name"] for u in uploads}))
    rows = []
    for spec in [NO_AI] + models:
        use_model(engine, spec, effort)
        row = {"model": spec, "items": [], "usd": 0.0, "seconds": 0.0}
        for pile, uploads, names in prepared:
            print(f"  sort  {spec:14} {pile['name']} ...", end=" ", flush=True)
            meter.reset()
            started = time.time()
            try:
                scored = score_sort(intake.sort(uploads), names, pile["expected"], today)
                if spec != NO_AI and scored["made_by"] != "ai":
                    scored["mistakes"].insert(0, "the AI call failed, so the keyword rules sorted the files")
            except Exception as error:  # one failed run shouldn't stop the whole test
                scored = {"grouping": 0, "types": 0, "dates": 0, "score": 0, "mistakes": [f"failed: {error}"]}
            scored.update(name=pile["name"], seconds=time.time() - started, usd=meter.usd, models=sorted(meter.models))
            row["items"].append(scored)
            print(f"{scored['score']:.0%} in {scored['seconds']:.0f}s")
        rows.append(summarise(row, ("grouping", "types", "dates")))
    return rows


def run_answers(questions: list[dict], models: list[str], engine: str, effort: str, client) -> list[dict]:
    rows = []
    for spec in models:
        use_model(engine, spec, effort)
        row = {"model": spec, "items": [], "usd": 0.0, "seconds": 0.0}
        for number, item in enumerate(questions, 1):
            print(f"  answer {spec:14} question {number} ...", end=" ", flush=True)
            started = time.time()
            chat = client.post("/api/chats", json={"title": "eval"}).json()   # a fresh chat: no earlier answers
            response = client.post(f"/api/chats/{chat['id']}/ask", json={"question": item["question"], "style": "detailed"})
            events = read_events(response.text)
            done = next((data for name, data in reversed(events) if name == "done"), None)
            models: list[str] = []
            if done and done.get("mode") == "ai":
                message = done["message"]
                scored = score_answer(message["content"], (message["data"].get("citations") or {}).get("citations", []), item)
                usd = float((message["data"].get("cost") or {}).get("usd") or 0)
                models = [message["data"]["model"]] if message["data"].get("model") else []
                scored["answer"] = message["content"]  # kept in the .json results, to read what was said
            else:
                failure = next((data.get("message") for name, data in events if name == "error"), "no answer")
                scored = {"score": 0, "cites_ok": 0, "cites": len(item.get("cite", [])), "made_up": 0, "mistakes": [f"failed: {failure}"]}
                usd = 0.0
            scored.update(name=f"Q{number}", question=item["question"], seconds=time.time() - started, usd=usd, models=models)
            row["items"].append(scored)
            print(f"{scored['score']:.0%} in {scored['seconds']:.0f}s")
        rows.append(summarise(row, ()))
    return rows


def read_events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


def summarise(row: dict, parts: tuple[str, ...]) -> dict:
    items = row["items"]
    count = len(items) or 1
    row["score"] = sum(i["score"] for i in items) / count
    row["usd"] = sum(i["usd"] for i in items) / count
    row["seconds"] = sum(i["seconds"] for i in items) / count
    for part in parts:
        row[part] = sum(i[part] for i in items) / count
    row["used"] = ", ".join(sorted({m for i in items for m in i.get("models", [])})) or "-"
    return row


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def report(sort_rows: list[dict], answer_rows: list[dict], engine: str, test_set: str, usd_to_inr: float) -> str:
    def money(usd: float) -> str:
        return f"${usd:.3f} (₹{usd * usd_to_inr:.2f})"

    cost_note = ("cost = what the same calls cost on the API (Claude Code itself uses your subscription)"
                 if engine == "claude_code" else "cost = charged to your API credits")
    lines = [f"# Model test, {datetime.now():%d %b %Y %H:%M}", "",
             f"Engine: {engine} · test set: {test_set} · {cost_note}.", ""]
    if sort_rows:
        lines += ["## Sorting a pile of files (the drop box)", "",
                  "| Model | Overall | Grouping | Document types | Dates | Time per pile | Cost per pile | Model that answered |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in sort_rows:
            lines.append(f"| {r['model']} | **{r['score']:.0%}** | {r['grouping']:.0%} | {r['types']:.0%} | {r['dates']:.0%} "
                         f"| {r['seconds']:.0f} s | {money(r['usd']) if r['model'] != NO_AI else 'free'} | {r['used']} |")
        pick = good_enough(sort_rows)
        if pick:
            lines += ["", f"**Good enough for sorting:** {pick['model']} (the cheapest within 5 points of the best)."]
        lines.append("")
    if answer_rows:
        lines += ["## Answering law questions", "",
                  "| Model | Overall | Right provisions cited | Red (made-up) citations | Time per answer | Cost per answer | Model that answered |",
                  "|---|---|---|---|---|---|---|"]
        for r in answer_rows:
            cites_ok = sum(i["cites_ok"] for i in r["items"])
            cites = sum(i["cites"] for i in r["items"])
            made_up = sum(i["made_up"] for i in r["items"])
            lines.append(f"| {r['model']} | **{r['score']:.0%}** | {cites_ok} of {cites} | {made_up} | {r['seconds']:.0f} s "
                         f"| {money(r['usd'])} | {r['used']} |")
        pick = good_enough(answer_rows)
        if pick:
            lines += ["", f"**Good enough for answers:** {pick['model']} (the cheapest within 5 points of the best)."]
        lines.append("")
    lines += ["## What each model got wrong", ""]
    for job, rows in (("Sorting", sort_rows), ("Answers", answer_rows)):
        for r in rows:
            wrong = [(i["name"], m) for i in r["items"] for m in i["mistakes"]]
            if wrong:
                lines.append(f"**{job}, {r['model']}:**")
                lines += [f"- {name}: {mistake}" for name, mistake in wrong]
                lines.append("")
    if not any(m for rows in (sort_rows, answer_rows) for r in rows for i in r["items"] for m in i["mistakes"]):
        lines.append("Nothing: every check passed.")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Which Claude model is good enough for each job?")
    parser.add_argument("--engine", choices=["claude_code", "api"], help="default: the engine the app uses now")
    parser.add_argument("--models", help="comma-separated; default: haiku,sonnet,opus (or the three API models)")
    parser.add_argument("--jobs", default="sort,answer", help="sort, answer or both (default)")
    parser.add_argument("--set", default="sample", help="sample (made-up files), private (your real files) or a folder")
    parser.add_argument("--piles", help="only the piles whose names contain these words (comma-separated), e.g. 3-harder")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    folder = Path(args.set) if Path(args.set).is_dir() else EVALS / args.set
    if not folder.is_dir():
        sys.exit(f"No test set at {folder}. See evals\\README.md.")
    # A fresh, separate workspace: your real cases are never touched
    shutil.rmtree(EVAL_WORKSPACE, ignore_errors=True)
    os.environ.update(WORKSPACE_DIR=str(EVAL_WORKSPACE), REMINDERS="0", INBOX="0", LAW_VECTORS="0")

    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    engine = args.engine or config.answer_engine()
    if engine not in ("claude_code", "api"):
        sys.exit("The model test needs AI: sign in to Claude Code, or add ANTHROPIC_API_KEY to .env.")
    config.ANSWER_ENGINE = engine
    if config.answer_engine() != engine:
        sys.exit(f"The {engine} engine isn't available here "
                 f"({'no ANTHROPIC_API_KEY in .env' if engine == 'api' else 'Claude Code not found'}).")
    models = [m.strip() for m in (args.models or DEFAULT_MODELS[engine]).split(",") if m.strip()]
    jobs = {j.strip() for j in args.jobs.split(",")}
    effort = config.CLAUDE_EFFORT
    today = date.today()
    meter = Meter()
    meter.install()

    sort_rows, answer_rows = [], []
    with TestClient(app) as client:
        if "sort" in jobs:
            piles = load_piles(folder, today)
            if args.piles:
                wanted = [w.strip() for w in args.piles.split(",") if w.strip()]
                piles = [p for p in piles if any(w in p["name"] for w in wanted)]
            print(f"Sorting: {len(piles)} piles x {len(models)} models (+ no AI)")
            sort_rows = run_sort(piles, models, engine, effort, meter, today) if piles else []
        if "answer" in jobs and (folder / "questions.json").exists():
            questions = json.loads((folder / "questions.json").read_text(encoding="utf-8"))["questions"]
            print(f"Answers: {len(questions)} questions x {len(models)} models")
            answer_rows = run_answers(questions, models, engine, effort, client)

    text = report(sort_rows, answer_rows, engine, folder.name, config.USD_TO_INR)
    results = EVALS / "results"
    results.mkdir(exist_ok=True)
    out = results / f"{datetime.now():%Y-%m-%d-%H%M}-{engine}-{folder.name}.md"
    out.write_text(text, encoding="utf-8")
    (results / f"{out.stem}.json").write_text(json.dumps({"sort": sort_rows, "answer": answer_rows}, indent=1, default=str),
                                              encoding="utf-8")
    print("\n" + text + f"\nSaved: {out}")
    shutil.rmtree(EVAL_WORKSPACE, ignore_errors=True)


if __name__ == "__main__":
    main()
