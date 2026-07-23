# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""
End-to-end tests: a real OpdWeather instrument querying, over real HTTP, the
weather API simulator - exactly as it would query the LNA weather service.
"""

import math

import pytest
import requests

from chimera_lna.instruments.opdweather import OpdWeather
from chimera_lna.simulators.weather import WeatherSimulator, synthetic_payload

API_PAYLOAD = {
    "id": 2169609,
    "datetime": "2026-07-12T18:00:00Z",
    "temperature": "10.70",
    "humidity": "100.00",
    "wind_speed": "19.30",
    "wind_dir": "WSW",
    "wind_angle": "247.50",
    "bar": "758.40",
    "solar_rad": "153.00",
    "uv_dose": "0.70",
    "wind_val": "10-20",
    "leaf": "15.00",
    "inside_temperature": None,
}


@pytest.fixture
def simulator():
    # serve a fixed payload so value assertions are deterministic
    with WeatherSimulator(payload=dict(API_PAYLOAD)) as simulator:
        yield simulator


@pytest.fixture
def weather(simulator):
    station = OpdWeather()
    station["api_url"] = simulator.url
    return station


class TestWeatherSimulatorHttp:
    """HTTP-level tests against the bare server."""

    def test_serves_payload(self, simulator):
        response = requests.get(simulator.url, timeout=5)
        assert response.status_code == 200
        assert response.json() == API_PAYLOAD

    def test_unknown_path_404s(self, simulator):
        base = simulator.url.replace("/api/weather-now/", "/nope")
        assert requests.get(base, timeout=5).status_code == 404

    def test_synthetic_payload_schema(self):
        with WeatherSimulator() as simulator:
            payload = requests.get(simulator.url, timeout=5).json()
        assert set(payload) == set(API_PAYLOAD)
        assert set(synthetic_payload()) == set(API_PAYLOAD)


class TestOpdWeather:
    """OpdWeather talking to the simulator over real HTTP."""

    def test_temperature(self, weather):
        assert weather.temperature() == pytest.approx(10.70)

    def test_humidity(self, weather):
        assert weather.humidity() == pytest.approx(100.0)

    def test_wind_speed_converted_to_ms(self, weather):
        assert weather.wind_speed() == pytest.approx(19.30 / 3.6)

    def test_wind_direction_in_degrees(self, weather):
        assert weather.wind_direction() == pytest.approx(247.5)

    def test_pressure_converted_to_pa(self, weather):
        assert weather.pressure() == pytest.approx(758.40 * 133.322387415)

    def test_dew_point_at_saturation_equals_temperature(self, weather):
        # At 100% relative humidity the dew point equals the temperature.
        assert weather.dew_point() == pytest.approx(weather.temperature(), abs=0.01)

    def test_last_measurement_time_is_fits_format(self, weather):
        assert weather.get_last_measurement_time() == "2026-07-12T18:00:00.000"

    def test_check_caches_data(self, weather, simulator):
        assert weather._check() is True
        # change what the server returns: within check_interval the cached
        # data must still be served
        simulator.payload = dict(API_PAYLOAD, temperature="99.99")
        assert weather.temperature() == pytest.approx(10.70)

    def test_api_down_returns_nan(self):
        simulator = WeatherSimulator(payload=dict(API_PAYLOAD)).start()
        station = OpdWeather()
        station["api_url"] = simulator.url
        station["request_timeout"] = 1
        simulator.stop()  # server is gone before the first query
        assert math.isnan(station.temperature())
        assert station.get_last_measurement_time() is None


class TestOpdWeatherLifecycle:
    """Full lifecycle through the chimera Manager and the HTTP simulator."""

    def test_manager_lifecycle(self, simulator, manager):
        weather = manager.add_class(
            OpdWeather, "opd", config={"api_url": simulator.url}
        )

        assert weather.temperature() == pytest.approx(10.70)
        assert weather.humidity() == pytest.approx(100.0)

        metadata = dict(
            (keyword, value) for keyword, value, _ in weather.get_metadata(None)
        )
        assert metadata["ENVMOD"] == "OPD 1.60m telescope weather station"
        assert metadata["ENVTEM"] == pytest.approx(10.70)
        assert metadata["ENVDATE"] == "2026-07-12T18:00:00.000"
        assert "ENVWIN" in metadata
        assert "ENVPRE" in metadata

        manager.remove("/OpdWeather/opd")
