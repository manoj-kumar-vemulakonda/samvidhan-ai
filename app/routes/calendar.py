"""Web API for the calendar (hearings, deadlines) and notifications."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import exports, reminders, store
from app.bus import bus

router = APIRouter()


class EventFields(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    kind: str | None = None
    date: str | None = None
    time: str | None = None
    location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)
    status: str | None = None
    case_id: int | None = None


@router.get("/api/events")
def list_events(start: str = Query(...), end: str = Query(...), case_id: int | None = None) -> list[dict]:
    return store.list_events(start, end, case_id)


@router.get("/api/agenda")
def agenda(days: int = Query(14, ge=1, le=120)) -> dict:
    """What the right-hand column shows: the next hearing, the coming days, and overdue items."""
    upcoming = store.upcoming_events(days)
    next_hearing = store.next_event(kinds=("hearing",))
    return {"next_hearing": next_hearing, "upcoming": upcoming, "overdue": store.overdue_events(),
            "today": date.today().isoformat()}


@router.post("/api/events")
def add_event(fields: EventFields) -> dict:
    data = fields.model_dump(exclude_none=True)
    if not data.get("date"):
        raise HTTPException(status_code=422, detail="Please choose a date.")
    try:
        event = store.add_event({"title": "Hearing", **data})
    except ValueError:
        raise HTTPException(status_code=422, detail="That date is not valid.")
    bus.publish("calendar", {"case_id": event.get("case_id")})
    return event


@router.patch("/api/events/{event_id}")
def update_event(event_id: int, fields: EventFields) -> dict:
    if not store.get_event(event_id):
        raise HTTPException(status_code=404, detail="Event not found.")
    try:
        event = store.update_event(event_id, fields.model_dump(exclude_none=True))
    except ValueError:
        raise HTTPException(status_code=422, detail="That date is not valid.")
    bus.publish("calendar", {"case_id": event.get("case_id")})
    return event


@router.delete("/api/events/{event_id}")
def delete_event(event_id: int) -> dict:
    event = store.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    store.delete_event(event_id)
    bus.publish("calendar", {"case_id": event.get("case_id")})
    return {"deleted": True}


@router.get("/api/calendar.ics")
def calendar_file(case_id: int | None = None) -> Response:
    start = (date.today() - timedelta(days=30)).isoformat()
    end = (date.today() + timedelta(days=730)).isoformat()
    events = [e for e in store.list_events(start, end, case_id) if e["status"] != "cancelled"]
    return Response(exports.calendar_ics(events), media_type="text/calendar",
                    headers={"Content-Disposition": 'attachment; filename="samvidhan-calendar.ics"'})


@router.get("/api/notifications")
def notifications(limit: int = Query(50, ge=1, le=200)) -> dict:
    return {"items": store.list_notifications(limit), "unread": store.unread_count()}


class ReadRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)


@router.post("/api/notifications/read")
def mark_read(request: ReadRequest) -> dict:
    store.mark_read(request.ids or None)
    return {"unread": store.unread_count()}


@router.post("/api/notifications/check")
def check_now() -> dict:
    """Run the reminder check right away (also runs by itself every 30 seconds)."""
    created = reminders.check()
    unread = store.unread_count()
    for notification in created:
        bus.publish("notification", {"notification": notification, "unread": unread})
    return {"created": created, "unread": unread}


@router.post("/api/notifications/test")
def test_notification() -> dict:
    name = store.display_name()
    notification = store.add_notification(
        f"test:{store.db.now()}", f"Test reminder{' for ' + name if name else ''}",
        "Desktop notifications are working. You'll see hearings and deadlines like this.", "info")
    unread = store.unread_count()
    bus.publish("notification", {"notification": notification, "unread": unread})
    return {"notification": notification, "unread": unread}
