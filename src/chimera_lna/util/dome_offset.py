# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Geometric dome azimuth correction. See docs/dome_synchronisation.pdf."""

import numpy as np

# At LNA:
#  dec_length: 49.2
#  dome_radius: 147
# r = 33 * np.cos((22+29)*np.pi/180.) -10  = 10.77 cm - From Paramount ME Manual
# R = 147 cm - LNA dome.


class CalcDomeError(Exception):
    pass


def calc_dome_az(ha, dec, phi, x0, y0, z0, dec_axis_length, dome_radius):
    """
    Calculates the corrected dome azimuth given the dome/telescope geometry.

    :param ha: Telescope hour-angle in radians
    :param dec: Telescope declination in radians
    :param phi: Telescope elevation of the polar axis (usually the site latitude) in radians
    :param x0: Telescope gravity center position X. See dome_synchronisation.pdf on documentation.
    :param y0: Telescope gravity center position Y. See dome_synchronisation.pdf on documentation.
    :param z0: Telescope gravity center position Z. See dome_synchronisation.pdf on documentation.
    :param dec_axis_length: Distance along the declination axis from the gravity center to the optical axis
    :param dome_radius: Dome radius
    :return dome_az: Corrected dome azimuth in radians.
    """

    # Calculate position of the optical axis origin with respect to dome center.
    x = x0 + dec_axis_length * np.cos(phi) * np.cos(ha)
    y = y0 - dec_axis_length * np.sin(phi) * np.sin(ha)
    z = z0 - dec_axis_length * np.cos(phi) * np.sin(ha)

    # unit vector with the direction of the optical axis (to the object)
    # 1. if observatory is in the North hemisphere, -1. if South
    pole_sign = 1.0 if phi > 0.0 else -1.0
    dang = (np.pi / 2.0 + (pole_sign * dec)) - (pole_sign * phi)

    vx = np.sin(ha) * np.sin(dang * np.pi / 180.0)
    vy = np.cos(dang * np.pi / 180.0) + np.zeros_like(ha)
    vz = np.cos(ha) * np.sin(dang * np.pi / 180.0) - np.cos(dang * np.pi / 180.0)

    # find the distance along the optical axis where it crosses the dome sphere
    path = np.linspace(dome_radius / 2.0, dome_radius * 2.0, 100)
    res = (
        (x + path * vx) ** 2.0
        + (y + path * vy) ** 2.0
        + (z + path * vz) ** 2.0
        - dome_radius**2.0
    )
    crossing = path[res.argmin()]

    v = np.arccos((z + crossing * vz) / dome_radius)
    ux = np.arccos((x + crossing * vx) / (dome_radius * np.sin(v)))
    dy = y + crossing * vy

    if dy < 0.0:
        ux = 2.0 * np.pi - ux

    return ux
