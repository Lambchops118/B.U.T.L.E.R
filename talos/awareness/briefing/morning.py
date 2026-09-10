"""Guaranteed, deterministic morning context for the daily briefing."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import sqlalchemy as sa

from talos.awareness.db.models import Reminder

WeatherProvider = Callable[[], Awaitable[str]]


def _local_now(settings, now: datetime) -> datetime:
    try:
        return now.astimezone(ZoneInfo(settings.timezone))
    except ZoneInfoNotFoundError:
        return now.astimezone()


def _safe_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text[:limit]


async def _weather(settings) -> str:
    if not settings.weather_api_key or not settings.weather_location:
        raise RuntimeError("weather_not_configured")
    params = {
        "q": settings.weather_location,
        "appid": settings.weather_api_key.get_secret_value(),
        "units": settings.weather_units,
    }
    async with httpx.AsyncClient(timeout=settings.morning_weather_timeout_seconds,
                                 trust_env=False, follow_redirects=False) as client:
        response = await client.get(
            "https://api.openweathermap.org/data/2.5/weather", params=params
        )
        response.raise_for_status()
        data = response.json()
    condition = _safe_text(((data.get("weather") or [{}])[0]).get("description"), 60)
    main = data.get("main") or {}
    temperature = main.get("temp")
    feels_like = main.get("feels_like")
    if not isinstance(temperature, (int, float)):
        raise RuntimeError("weather_temperature_missing")
    unit = "degrees Fahrenheit" if settings.weather_units == "imperial" else (
        "degrees Celsius" if settings.weather_units == "metric" else "kelvin"
    )
    location = _safe_text(data.get("name") or settings.weather_location, 60)
    sentence = f"In {location}, it is {temperature:g} {unit}"
    if condition:
        sentence += f" with {condition}"
    if isinstance(feels_like, (int, float)) and feels_like != temperature:
        sentence += f", and it feels like {feels_like:g}"
    return sentence + "."


async def _todays_reminders(engine, settings, local: datetime) -> tuple[list[Any], bool]:
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    limit = min(settings.morning_agenda_max_items, settings.max_query_points)
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                sa.select(Reminder.reminder_id, Reminder.text, Reminder.due_at)
                .where(
                    Reminder.status == "scheduled",
                    Reminder.due_at >= start_local.astimezone(timezone.utc),
                    Reminder.due_at < end_local.astimezone(timezone.utc),
                )
                .order_by(Reminder.due_at, Reminder.reminder_id)
                .limit(limit + 1)
            )
        ).all()
    return list(rows[:limit]), len(rows) > limit


async def build_morning_context(
    engine,
    settings,
    *,
    now: datetime | None = None,
    weather_provider: WeatherProvider | None = None,
) -> dict[str, Any]:
    """Build time, weather, and today's scheduled reminders with an audit.

    The result is frozen into the briefing outbox before delivery, so a retry
    repeats the same words rather than changing underneath the receipt.
    """
    now = now or datetime.now(timezone.utc)
    local = _local_now(settings, now)
    sentences = [
        f"Good morning. It is {local.strftime('%A, %B')} {local.day} at "
        f"{local.strftime('%I:%M %p').lstrip('0')}."
    ]
    audit: dict[str, Any] = {
        "version": "morning-context-v1",
        "as_of": now.isoformat(),
        "timezone": str(getattr(local.tzinfo, "key", local.tzinfo)),
        "weather": "unavailable",
        "agenda_query": "morning.scheduled_reminders.v1",
    }
    try:
        weather_text = await (weather_provider or (lambda: _weather(settings)))()
        sentences.append(_safe_text(weather_text, 300))
        audit["weather"] = "current_observation"
    except Exception as exc:
        sentences.append("I could not retrieve the current weather.")
        audit["weather_error"] = type(exc).__name__

    try:
        reminders, truncated = await _todays_reminders(engine, settings, local)
        audit.update(agenda_count=len(reminders), agenda_truncated=truncated)
        if reminders:
            entries = []
            for reminder in reminders:
                due = reminder.due_at.astimezone(local.tzinfo).strftime("%I:%M %p").lstrip("0")
                entries.append(f"{due}: {_safe_text(reminder.text)}")
            sentences.append("Today's scheduled reminders are " + "; ".join(entries) + ".")
        else:
            sentences.append("You have no scheduled reminders for today.")
    except Exception as exc:
        sentences.append("I could not retrieve today's scheduled reminders.")
        audit.update(agenda_count=None, agenda_truncated=False,
                     agenda_error=type(exc).__name__)
    return {"text": " ".join(sentences), "audit": audit}

