# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""OPD/LNA weather station instrument backed by the LNA weather HTTP API."""

import datetime
import math
import time

import requests
import urllib3
from chimera.core.lock import lock
from chimera.instruments.weatherstation import WeatherStationBase
from chimera.interfaces.weatherstation import (
    WeatherHumidity,
    WeatherPressure,
    WeatherTemperature,
    WeatherWind,
)

MMHG_TO_PA = 133.322387415
KMH_TO_MS = 1 / 3.6


class OpdWeather(
    WeatherStationBase,
    WeatherTemperature,
    WeatherHumidity,
    WeatherPressure,
    WeatherWind,
):
    """
    Weather station of the 1.60m telescope at OPD/LNA.

    Queries the LNA weather API, which returns a JSON payload like:

    {"id": 2169609, "datetime": "2026-07-12T18:00:00Z", "temperature": "10.70",
     "humidity": "100.00", "wind_speed": "19.30", "wind_dir": "WSW",
     "wind_angle": "247.50", "bar": "758.40", "solar_rad": "153.00", ...}

    Native units: temperature deg_C, humidity %, wind_speed km/h,
    wind_angle deg, bar mmHg.
    """

    __config__ = {
        "model": "OPD 1.60m telescope weather station",
        "api_url": "https://200.131.64.237:8088/api/weather-now/",
        "check_interval": 3 * 60,  # in seconds
        "request_timeout": 30,  # in seconds
        "verify_ssl": False,  # the LNA API uses a self-signed certificate
    }

    def __init__(self):
        WeatherStationBase.__init__(self)
        self._last_check = 0.0
        self._data = None

    def __start__(self):
        self.set_hz(1.0 / self["check_interval"])

    def _fetch(self) -> dict:
        """
        Query the LNA weather API.
        :return: the decoded JSON payload as a dict.
        """
        self.log.debug(f"Querying OPD weather API at {self['api_url']}...")
        if not self["verify_ssl"]:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(
            self["api_url"],
            timeout=self["request_timeout"],
            verify=self["verify_ssl"],
        )
        response.raise_for_status()
        return response.json()

    @lock
    def _check(self) -> bool:
        if (
            self._data is not None
            and time.time() < self._last_check + self["check_interval"]
        ):
            return True

        try:
            self._data = self._fetch()
        except (requests.RequestException, ValueError) as e:
            self.log.error(f"Error querying weather API {self['api_url']}: {e}")
            return False

        self._last_check = time.time()
        return True

    def control(self) -> bool:
        self._check()
        return True

    def _value(self, key: str) -> float:
        self._check()
        if self._data is None or self._data.get(key) is None:
            return float("nan")
        try:
            return float(self._data[key])
        except (TypeError, ValueError):
            self.log.warning(f"Invalid value for '{key}': {self._data[key]}")
            return float("nan")

    def get_last_measurement_time(self) -> str | None:
        """
        UTC time of the last measurement as a FITS format string
        ("YYYY-MM-DDThh:mm:ss.sss").
        """
        self._check()
        if self._data is None or not self._data.get("datetime"):
            return None
        dt = datetime.datetime.fromisoformat(self._data["datetime"])
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]

    def temperature(self) -> float:
        return self._value("temperature")

    def dew_point(self) -> float:
        # The API does not report the dew point: derive it from temperature and
        # relative humidity using the Magnus formula.
        temp = self.temperature()
        hum = self.humidity()
        if math.isnan(temp) or math.isnan(hum) or hum <= 0:
            return float("nan")
        a, b = 17.62, 243.12
        gamma = math.log(hum / 100.0) + a * temp / (b + temp)
        return b * gamma / (a - gamma)

    def humidity(self) -> float:
        return self._value("humidity")

    def wind_speed(self) -> float:
        return self._value("wind_speed") * KMH_TO_MS

    def wind_direction(self) -> float:
        return self._value("wind_angle")

    def pressure(self) -> float:
        return self._value("bar") * MMHG_TO_PA


if __name__ == "__main__":
    weather = OpdWeather()
    for keyword, value, comment in weather.get_metadata(None):
        print(f"{keyword:8s} = {value} / {comment}")
