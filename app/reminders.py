"""
Personalised reminders. A small loop inside the app checks the calendar every 30 seconds
and creates notifications when they are due - following YOUR settings:

  * daily agenda at your morning time ("Good morning, Adv. Sharma - 2 hearings today")
  * hearings: the evening before, the morning of, and N minutes before (if a time is set)
  * deadlines and limitation dates: a countdown (7, 3, 1 days before) and on the day
  * overdue deadlines that were never marked done

Each reminder has a unique key, so it is never sent twice - even after a restart.
Reminders only appear while the app is running (a local app can't wake itself up).
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import date, datetime, time, timedelta

from app import store
from app.bus import bus

CATCH_UP = timedelta(hours=14)   # a reminder missed while the app was closed is still shown if this recent


def _at(day: date, hhmm: str, fallback: str = "08:00") -> datetime:
    try:
        hours, minutes = (int(x) for x in (hhmm or fallback).split(":"))
    except ValueError:
        hours, minutes = (int(x) for x in fallback.split(":"))
    return datetime.combine(day, time(hours, minutes))


def _when(event: dict) -> str:
    day = date.fromisoformat(event["date"])
    text = day.strftime("%a %d %b")
    return f"{text}, {event['time']}" if event.get("time") else text


def _where(event: dict) -> str:
    return event.get("location") or event.get("case_court") or ""


def _case(event: dict) -> str:
    return f" - {event['case_title']}" if event.get("case_title") else ""


def due_reminders(now: datetime, settings: dict, events: list[dict]) -> list[dict]:
    """Every reminder whose time has come (pure function - easy to test)."""
    out: list[dict] = []
    today = now.date()
    name = store.display_name(settings)
    morning, evening = settings.get("morning_time", "08:00"), settings.get("evening_time", "19:00")

    def add(key: str, at: datetime, title: str, body: str, event: dict | None = None, kind: str = "reminder") -> None:
        if at <= now <= at + CATCH_UP:
            out.append({"key": key, "title": title, "body": body, "kind": kind,
                        "case_id": event.get("case_id") if event else None, "event_id": event.get("id") if event else None})

    open_events = [e for e in events if e["status"] == "open"]
    # 1. The daily agenda
    if settings.get("daily_agenda", True):
        todays = [e for e in open_events if e["date"] == today.isoformat()]
        if todays:
            hearings = sum(1 for e in todays if e["kind"] == "hearing")
            others = len(todays) - hearings
            parts = []
            if hearings:
                parts.append(f"{hearings} hearing{'s' if hearings != 1 else ''}")
            if others:
                parts.append(f"{others} other item{'s' if others != 1 else ''}")
            greeting = f"Good morning{', ' + name if name else ''}"
            body = "\n".join(f"• {e['time'] or 'All day'} {e['title']}{_case(e)}" + (f" ({_where(e)})" if _where(e) else "")
                             for e in todays)
            add(f"agenda:{today}", _at(today, morning), f"{greeting}: {' and '.join(parts)} today", body, kind="agenda")
    for event in open_events:
        day = date.fromisoformat(event["date"])
        where = _where(event)
        if event["kind"] in ("hearing", "meeting"):
            label = "Hearing" if event["kind"] == "hearing" else "Meeting"
            if settings.get("remind_evening_before", True):
                add(f"ev{event['id']}:eve:{event['date']}", _at(day - timedelta(days=1), evening),
                    f"Tomorrow{(' ' + event['time']) if event['time'] else ''}: {label.lower()}{_case(event)}",
                    f"{event['title']}" + (f" · {where}" if where else "") + " · Open the case to prepare a hearing brief.",
                    event)
            if settings.get("remind_morning_of", True):
                add(f"ev{event['id']}:morning:{event['date']}", _at(day, morning),
                    f"Today{(' ' + event['time']) if event['time'] else ''}: {label.lower()}{_case(event)}",
                    event["title"] + (f" · {where}" if where else ""), event)
            minutes = int(settings.get("remind_minutes_before") or 0)
            if minutes and event.get("time"):
                start = _at(day, event["time"])
                add(f"ev{event['id']}:soon:{event['date']}{event['time']}", start - timedelta(minutes=minutes),
                    f"In {minutes} minutes: {label.lower()}{_case(event)}", event["title"] + (f" · {where}" if where else ""),
                    event)
        else:
            what = {"deadline": "Deadline", "limitation": "Limitation ends", "reminder": "Reminder"}.get(event["kind"], "Due")
            for days in settings.get("deadline_days") or [7, 3, 1]:
                add(f"ev{event['id']}:minus{days}:{event['date']}", _at(day - timedelta(days=int(days)), morning),
                    f"{days} day{'s' if int(days) != 1 else ''} left - {what.lower()}{_case(event)}",
                    f"{event['title']} · due {_when(event)}", event)
            add(f"ev{event['id']}:due:{event['date']}", _at(day, morning), f"Due today - {event['title']}{_case(event)}",
                f"{what} today" + (f" · {where}" if where else ""), event)
            # Overdue: the day after, if still not done
            add(f"ev{event['id']}:overdue:{event['date']}", _at(day + timedelta(days=1), morning),
                f"Overdue - {event['title']}{_case(event)}",
                f"This was due on {_when(event)}. Mark it done or move the date.", event, kind="overdue")
    return out


def check(now: datetime | None = None) -> list[dict]:
    """Create the reminders that are due now; returns the new notifications."""
    now = now or datetime.now()
    settings = store.get_settings()
    longest = max([int(d) for d in settings.get("deadline_days") or [7]] + [1])
    events = store.list_events((now.date() - timedelta(days=2)).isoformat(),
                               (now.date() + timedelta(days=longest + 1)).isoformat())
    created = []
    for reminder in due_reminders(now, settings, events):
        notification = store.add_notification(reminder["key"], reminder["title"], reminder["body"], reminder["kind"],
                                              reminder["case_id"], reminder["event_id"])
        if notification:
            created.append(notification)
    return created


async def run_forever(interval: float = 30.0) -> None:
    """The background loop (started by app/main.py)."""
    while True:
        try:
            created = await asyncio.to_thread(check)
            if created:
                unread = await asyncio.to_thread(store.unread_count)
                for notification in created:
                    bus.publish("notification", {"notification": notification, "unread": unread})
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(interval)
