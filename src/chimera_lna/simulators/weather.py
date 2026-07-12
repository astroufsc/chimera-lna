# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""
HTTP simulator of the LNA weather API.

Serves GET /api/weather-now/ with the same JSON schema as the real API so
that OpdWeather can query it exactly as it would query the real service:

    weatherstations:
      - name: opd_weather
        type: OpdWeather
        api_url: http://127.0.0.1:8088/api/weather-now/

Run it standalone with:

    python -m chimera_lna.simulators.weather --port 8088
"""

import argparse
import datetime
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WIND_ROSE = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
]


def synthetic_payload():
    """
    Synthetic weather payload with the same schema as the LNA weather API.
    Values vary smoothly with the time of day.
    """
    now = datetime.datetime.now(datetime.UTC)
    hour_angle = (math.pi / 12.0) * (now.hour + now.minute / 60.0)

    temperature = 10.0 + 8.0 * math.sin(hour_angle - math.pi / 2.0)
    humidity = 60.0 + 30.0 * math.cos(hour_angle)
    wind_speed = 15.0 + 10.0 * math.sin(hour_angle)  # km/h
    wind_angle = (180.0 + 180.0 * math.sin(hour_angle)) % 360.0
    wind_dir = WIND_ROSE[int((wind_angle + 11.25) // 22.5) % len(WIND_ROSE)]
    pressure = 758.0 + 2.0 * math.sin(hour_angle)  # mmHg

    # Mimic the API payload: all measurements are encoded as strings.
    return {
        "id": 0,
        "datetime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "temperature": f"{temperature:.2f}",
        "humidity": f"{humidity:.2f}",
        "wind_speed": f"{wind_speed:.2f}",
        "wind_dir": wind_dir,
        "wind_angle": f"{wind_angle:.2f}",
        "bar": f"{pressure:.2f}",
        "solar_rad": "153.00",
        "uv_dose": "0.70",
        "wind_val": "10-20",
        "leaf": "15.00",
        "inside_temperature": None,
    }


class _WeatherRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (name mandated by BaseHTTPRequestHandler)
        if self.path.rstrip("/") != "/api/weather-now":
            self.send_error(404, "Not Found")
            return

        payload = self.server.simulator.get_payload()
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # keep the simulator quiet


class WeatherSimulator:
    """
    End-to-end simulator of the LNA weather API.

    By default it serves a synthetic payload that varies with the time of day.
    Set `payload` (constructor argument or attribute) to a dict to serve fixed
    values instead - handy for deterministic tests.
    """

    def __init__(self, host="127.0.0.1", port=0, payload=None):
        self._host = host
        self._port = port
        self.payload = payload
        self._server = None
        self._thread = None

    def get_payload(self):
        return self.payload if self.payload is not None else synthetic_payload()

    # lifecycle

    def start(self):
        self._server = ThreadingHTTPServer(
            (self._host, self._port), _WeatherRequestHandler
        )
        self._server.simulator = self
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="WeatherSimulator", daemon=True
        )
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join()
            self._server = None
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def url(self):
        """URL of the weather-now endpoint served by this simulator."""
        return f"http://{self._host}:{self.port}/api/weather-now/"


def main(args=None):
    parser = argparse.ArgumentParser(description="LNA weather API simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    options = parser.parse_args(args)

    simulator = WeatherSimulator(host=options.host, port=options.port)
    simulator.start()
    print(f"LNA weather API simulator listening on {simulator.url}")
    print(f'Use api_url: "{simulator.url}" in the OpdWeather configuration.')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        simulator.stop()


if __name__ == "__main__":
    main()
