from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.services import home_automation


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise home_automation.requests.HTTPError(response=self)


class HomeAutomationWeatherTests(unittest.TestCase):
    def test_canonicalizes_city_state_query_for_us_geocoding(self) -> None:
        with mock.patch.object(home_automation, "OPEN_WEATHER_API_KEY", "test-key"), mock.patch(
            "talos.services.home_automation.requests.get",
            return_value=_FakeResponse([{"name": "Baltimore", "state": "Maryland", "country": "US", "lat": 39.2904, "lon": -76.6122}]),
        ) as get_mock:
            result = home_automation._resolve_location("Baltimore MD")

        self.assertEqual(result["name"], "Baltimore")
        self.assertEqual(get_mock.call_args.kwargs["params"]["q"], "Baltimore,MD,US")

    def test_uses_zip_geocoding_for_us_zip_code(self) -> None:
        with mock.patch.object(home_automation, "OPEN_WEATHER_API_KEY", "test-key"), mock.patch(
            "talos.services.home_automation.requests.get",
            return_value=_FakeResponse({"zip": "21043", "name": "Ellicott City", "country": "US", "lat": 39.2673, "lon": -76.7983}),
        ) as get_mock:
            result = home_automation._resolve_location("21043")

        self.assertEqual(result["name"], "Ellicott City")
        self.assertEqual(get_mock.call_args.kwargs["params"]["zip"], "21043,US")

    def test_get_current_weather_uses_geocoding_then_lat_lon_weather(self) -> None:
        geocode_payload = [{"name": "Baltimore", "state": "Maryland", "country": "US", "lat": 39.2904, "lon": -76.6122}]
        current_payload = {
            "coord": {"lat": 39.2904, "lon": -76.6122},
            "weather": [{"main": "Clear", "description": "clear sky"}],
            "main": {"temp": 76.0, "feels_like": 77.0, "humidity": 72, "temp_min": 74.0, "temp_max": 79.0, "pressure": 1015},
            "wind": {"speed": 3.7},
            "clouds": {"all": 0},
            "sys": {"country": "US", "sunrise": 100, "sunset": 200},
            "timezone": -14400,
            "dt": 150,
            "name": "Baltimore",
            "visibility": 10000,
        }
        onecall_payload = {
            "timezone": "America/New_York",
            "current": {"uvi": 8, "dew_point": 66.0},
            "daily": [{"pop": 0.2, "temp": {"max": 82.0, "min": 68.0}}],
        }

        responses = [
            _FakeResponse(geocode_payload),
            _FakeResponse(current_payload),
            _FakeResponse(onecall_payload),
        ]

        with mock.patch.object(home_automation, "OPEN_WEATHER_API_KEY", "test-key"), mock.patch(
            "talos.services.home_automation.requests.get",
            side_effect=responses,
        ) as get_mock:
            weather = home_automation.get_current_weather("Baltimore, MD")

        self.assertIn("Location: Baltimore, Maryland, US", weather)
        current_call = get_mock.call_args_list[1]
        self.assertEqual(current_call.kwargs["params"]["lat"], 39.2904)
        self.assertEqual(current_call.kwargs["params"]["lon"], -76.6122)
        self.assertNotIn("q", current_call.kwargs["params"])


if __name__ == "__main__":
    unittest.main()
