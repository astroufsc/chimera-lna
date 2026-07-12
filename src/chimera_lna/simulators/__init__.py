# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Standalone hardware simulators for the LNA plugin.

These are NOT chimera objects: they are real servers that emulate the
observatory hardware end-to-end.

- simulators.dome: TCP server that speaks the dome controller "MEADE" serial
  protocol. Point DomeLNA at it with device: socket://host:port.
- simulators.weather: HTTP server that mimics the LNA weather API. Point
  OpdWeather at it with api_url: http://host:port/api/weather-now/.
"""
