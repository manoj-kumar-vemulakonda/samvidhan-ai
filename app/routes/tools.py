"""Web API for settings, live updates, workflows (Skills) and review tables."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import assistant, config, exports, files, llm, retrieval, review, store, workflows
from app.bus import bus
from app.routes.common import SSE_HEADERS, need_case, need_chat, sse, state

router = APIRouter()


# ---------------------------------------------------------------------------
# Status, settings, live updates
# ---------------------------------------------------------------------------
@router.get("/api/status")
def status() -> dict:
    constitution = state["engine"].constitution
    laws = state.get("laws")
    engine = config.answer_engine()
    return {
        "app": config.APP_NAME,
        "ai_enabled": engine != "off",
        "engine": engine,
        "engine_label": config.engine_label(),
        "model": {"api": config.CLAUDE_MODEL, "claude_code": config.CLAUDE_CODE_MODEL or None}.get(engine),
        "effort": config.CLAUDE_EFFORT,
        "articles": len(constitution.articles),
        "schedule_pieces": len(constitution.schedules),
        "source": constitution.source,
        "max_question_chars": config.MAX_QUESTION_CHARS,
        "acts": len(laws.library.acts) if laws else 0,
        "sections": len(laws.library.sections) if laws else 0,
        "law_index": {"status": laws.status, "progress": round(laws.progress, 3)} if laws else None,
        "workspace": str(config.WORKSPACE_DIR),
        "max_upload_mb": config.MAX_UPLOAD_MB,
    }


@router.get("/api/settings")
def get_settings() -> dict:
    return {**store.get_settings(), "display_name": store.display_name()}


@router.put("/api/settings")
def save_settings(changes: dict) -> dict:
    settings = store.update_settings(changes)
    bus.publish("settings", {})
    return {**settings, "display_name": store.display_name(settings)}


@router.get("/api/stream")
async def stream() -> StreamingResponse:
    """One live stream per open page: uploads, plans, reminders, review progress..."""
    return StreamingResponse(bus.listen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------------------------------------------------------------------------
# Skills (workflow files)
# ---------------------------------------------------------------------------
@router.get("/api/skills")
def list_skills() -> list[dict]:
    return [s.to_dict() for s in workflows.load_skills()]


@router.get("/api/skills/{skill_id}")
def get_skill(skill_id: str) -> dict:
    skill = workflows.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return skill.to_dict(full=True)


class SkillText(BaseModel):
    source: str = Field(min_length=20, max_length=40000)


@router.put("/api/skills/{skill_id}")
def save_skill(skill_id: str, request: SkillText) -> dict:
    try:
        return workflows.save_skill(request.source, original_id=skill_id).to_dict(full=True)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/api/skills")
def new_skill(request: SkillText) -> dict:
    try:
        skill = workflows.parse_skill(request.source)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if workflows.get_skill(skill.id):
        raise HTTPException(status_code=400, detail=f"A workflow with the id '{skill.id}' already exists - change the id.")
    return workflows.save_skill(request.source).to_dict(full=True)


@router.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str) -> dict:
    skill = workflows.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    path = Path(skill.path)
    trash = config.SKILLS_DIR / "_removed"
    trash.mkdir(exist_ok=True)
    path.replace(files.unique_path(trash / path.name))
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Workflow runs: plan -> approve -> run
# ---------------------------------------------------------------------------
class StartRun(BaseModel):
    chat_id: int
    skill: str
    inputs: dict = Field(default_factory=dict)


@router.post("/api/runs")
async def start_run(request: StartRun) -> dict:
    chat = need_chat(request.chat_id)
    skill = workflows.get_skill(request.skill)
    if not skill:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    inputs = dict(request.inputs)
    inputs["document_ids"] = [int(d) for d in inputs.get("document_ids") or []][:20]
    run = store.create_run(chat["id"], chat["case_id"], skill.id, inputs)
    message = store.add_message(chat["id"], "workflow", "", {"run_id": run["id"], "title": skill.title, "icon": skill.icon})
    store.update_run(run["id"], message_id=message["id"])
    bus.publish("message", {"chat_id": chat["id"], "message": {**message, "run": store.get_run(run["id"])}})

    def plan_it() -> None:
        store.update_run(run["id"], status="planning")
        workflows.make_plan(run["id"])
        bus.publish("run", {"run_id": run["id"], "chat_id": chat["id"], "status": "waiting"})

    threading.Thread(target=plan_it, daemon=True).start()
    return {"run": store.get_run(run["id"]), "message": message}


@router.get("/api/runs/{run_id}")
def get_run(run_id: int) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found.")
    return run


class ApproveRun(BaseModel):
    steps: list[dict] = Field(default_factory=list)
    answers: dict = Field(default_factory=dict)


@router.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: int) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found.")
    run = store.update_run(run_id, status="cancelled")
    bus.publish("run", {"run_id": run_id, "chat_id": run["chat_id"], "status": "cancelled"})
    return run


@router.post("/api/runs/{run_id}/execute")
async def execute_run(run_id: int, request: ApproveRun) -> StreamingResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found.")
    if run["status"] not in ("waiting", "failed", "done"):
        raise HTTPException(status_code=400, detail="This workflow is not waiting for approval.")
    skill = workflows.get_skill(run["skill"])
    if not skill:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    steps = [s for s in request.steps if str(s.get("title", "")).strip()] or run["plan"].get("steps", [])
    plan = {**run["plan"], "steps": steps, "answers": request.answers}
    store.update_run(run_id, status="running", plan=plan)
    chat = store.get_chat(run["chat_id"])
    settings = store.get_settings()
    query = f"{skill.title}. " + " ".join(str(v) for k, v in run["inputs"].items() if k != "document_ids" and v) + " " + \
            " ".join(s.get("title", "") for s in steps)
    sources = await run_in_threadpool(retrieval.gather, query[:1500], state["engine"], state.get("laws"),
                                      chat["case_id"], None if chat["case_id"] else chat["id"])
    extra, material = workflows.execution_prompts(run, skill, steps, request.answers)
    system = assistant.system_prompt(settings, "detailed", assistant.answer_language("", "english", settings)) + extra
    prompt = assistant.build_prompt(f"Run the workflow \"{skill.title}\" now, following the approved plan.", sources, [], material)
    sources_data = sources.to_dict()

    async def events():
        yield sse("start", {"run": store.get_run(run_id)})
        yield sse("sources", sources_data)
        if not llm.available():
            store.update_run(run_id, status="failed", output="")
            yield sse("error", {"message": "AI is off - a workflow needs Claude Code or an API key."})
            return
        pieces: list[str] = []
        final, failure = None, ""
        finished = False
        try:
            async for event in llm.stream(system, prompt):
                if event["type"] == "delta":
                    pieces.append(event["text"])
                    yield sse("delta", {"text": event["text"]})
                elif event["type"] == "done":
                    final = event
                else:
                    failure = event.get("message", "Something went wrong.")
            output = "".join(pieces)
            library = state["laws"].library if state.get("laws") else None
            report = assistant.check_answer(output, sources, library, state["engine"].cases)
            status = "failed" if failure and not output else "done"
            store.update_run(run_id, status=status, output=output)
            if run.get("message_id"):
                store.update_message(run["message_id"], content=output, data={
                    "sources": sources_data, "citations": report, "model": (final or {}).get("model"),
                    "cost": (final or {}).get("cost"), "error": failure or None, "title": skill.title})
            finished = True
            bus.publish("run", {"run_id": run_id, "chat_id": run["chat_id"], "status": status})
            if status == "failed":
                yield sse("error", {"message": failure})
            else:
                yield sse("done", {"run": store.get_run(run_id), "citations": report, "message_id": run.get("message_id"),
                                   "model": (final or {}).get("model"), "cost": (final or {}).get("cost")})
        finally:
            if not finished:
                store.update_run(run_id, status="failed", output="".join(pieces))
                if run.get("message_id") and pieces:
                    store.update_message(run["message_id"], content="".join(pieces) + "\n\n*(stopped)*")

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------------------------------------------------------------------------
# Review tables / due diligence
# ---------------------------------------------------------------------------
@router.get("/api/review-templates")
def review_templates() -> list[dict]:
    return [{"id": key, "title": value["title"], "columns": review.columns_for(key) if key != "custom" else []}
            for key, value in review.TEMPLATES.items()]


class NewReview(BaseModel):
    template: str = "litigation"
    title: str = Field(default="", max_length=200)
    columns: list[dict] = Field(default_factory=list)
    document_ids: list[int] = Field(default_factory=list)


@router.get("/api/cases/{case_id}/reviews")
def list_reviews(case_id: int) -> list[dict]:
    need_case(case_id)
    return store.list_reviews(case_id)


@router.post("/api/cases/{case_id}/reviews")
def create_review(case_id: int, request: NewReview) -> dict:
    need_case(case_id)
    documents = {d["id"] for d in store.list_documents(case_id=case_id)}
    chosen = [d for d in request.document_ids if d in documents] or sorted(documents)
    if not chosen:
        raise HTTPException(status_code=400, detail="This case has no filed documents to review yet.")
    template = request.template if request.template in review.TEMPLATES else "custom"
    columns = review.columns_for(template, request.columns if (template == "custom" or request.columns) else None)
    if not columns:
        raise HTTPException(status_code=400, detail="Add at least one column (a question).")
    title = request.title.strip() or review.TEMPLATES[template]["title"]
    created = store.create_review(case_id, title, template, columns, chosen[:60])
    review.start(created["id"])
    bus.publish("review", {"review_id": created["id"], "status": "running"})
    return _review_payload(created["id"])


def _review_payload(review_id: int) -> dict:
    found = store.get_review(review_id)
    if not found:
        raise HTTPException(status_code=404, detail="Review not found.")
    documents = {d: store.get_document(d) for d in found["document_ids"]}
    return {**found, "documents": [d for d in documents.values() if d], "risk": review.risk_summary(found)}


@router.get("/api/reviews/{review_id}")
def get_review(review_id: int) -> dict:
    return _review_payload(review_id)


@router.post("/api/reviews/{review_id}/rerun")
def rerun_review(review_id: int) -> dict:
    found = store.get_review(review_id)
    if not found:
        raise HTTPException(status_code=404, detail="Review not found.")
    for cell in found["cells"]:
        store.set_cell(review_id, cell["document_id"], cell["column_key"], status="pending", answer="", quote="", risk="")
    store.update_review(review_id, report="")
    review.start(review_id)
    return _review_payload(review_id)


@router.delete("/api/reviews/{review_id}")
def delete_review(review_id: int) -> dict:
    store.delete_review(review_id)
    return {"deleted": True}


@router.get("/api/reviews/{review_id}/xlsx")
def review_excel(review_id: int) -> Response:
    found = store.get_review(review_id)
    if not found:
        raise HTTPException(status_code=404, detail="Review not found.")
    documents = {d: store.get_document(d) for d in found["document_ids"]}
    content = exports.review_xlsx(found, {k: v for k, v in documents.items() if v})
    name = files.safe_name(found["title"], 80) + ".xlsx"
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
