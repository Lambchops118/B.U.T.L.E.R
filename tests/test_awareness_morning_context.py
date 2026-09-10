"""Guaranteed time/weather/agenda content for the scheduled morning briefing."""

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from talos.awareness.briefing.morning import build_morning_context


def settings():
    return SimpleNamespace(
        timezone="America/New_York",
        weather_api_key=None,
        weather_location="Baltimore,US",
        weather_units="imperial",
        morning_weather_timeout_seconds=1,
        morning_agenda_max_items=5,
        max_query_points=100,
    )


class MorningContextTest(unittest.TestCase):
    def test_time_weather_and_empty_agenda_are_always_rendered(self):
        async def flow():
            with patch(
                "talos.awareness.briefing.morning._todays_reminders",
                new=AsyncMock(return_value=([], False)),
            ):
                result = await build_morning_context(
                    None,
                    settings(),
                    now=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
                    weather_provider=AsyncMock(
                        return_value="In Baltimore, it is 68 degrees Fahrenheit with clear skies."
                    ),
                )
            self.assertIn("Wednesday, September 9 at 8:00 AM", result["text"])
            self.assertIn("68 degrees Fahrenheit", result["text"])
            self.assertIn("no scheduled reminders", result["text"])
            self.assertEqual(result["audit"]["weather"], "current_observation")
        asyncio.run(flow())

    def test_weather_failure_is_truthful_and_does_not_drop_morning_context(self):
        async def fail_weather():
            raise RuntimeError("offline")

        async def flow():
            with patch(
                "talos.awareness.briefing.morning._todays_reminders",
                new=AsyncMock(return_value=([], False)),
            ):
                result = await build_morning_context(
                    None,
                    settings(),
                    now=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
                    weather_provider=fail_weather,
                )
            self.assertIn("could not retrieve the current weather", result["text"])
            self.assertEqual(result["audit"]["weather_error"], "RuntimeError")
        asyncio.run(flow())


if __name__ == "__main__":
    unittest.main()
