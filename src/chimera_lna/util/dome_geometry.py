# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Geometric dome-slaving model for a German equatorial mount."""

import math


class DomeGeometry:
    """
    Dome azimuth for a telescope on a German equatorial mount whose axes are
    offset from the dome center.

    Frame: the dome is a unit sphere centered at the origin, x=East, y=North,
    z=up; every length is in units of the dome radius. The optical tube axis
    passes through

        T = mount_offset + side * gem_offset * d

    where ``mount_offset`` is the intersection of the mount axes,
    ``d = polar_axis x pointing`` (normalized) is the declination axis
    direction, and ``side`` is +1 when the hour angle is positive (target west
    of the meridian) and -1 east of it, flipping with the pier side. The dome
    azimuth is the azimuth of the intersection of the ray from T along the
    pointing with the sphere, plus ``az_offset`` (absorbs the tag-ring
    zero-point error).

    Defaults were fitted to the 294-point 2016 dome mapping run stored in
    data/model.csv (median residual 1.4 deg transverse to the slit, at the
    2 deg/tag quantization floor). Refit with scripts/fit_dome_geometry.py;
    the derivation and fit plots are in docs/dome_geometry.md.
    """

    def __init__(
        self,
        latitude=-22.5344,
        mount_offset=(-0.0384, -0.0142, -0.4356),
        gem_offset=-0.3899,
        az_offset=19.73,
    ):
        self.latitude = latitude
        self.mount_offset = tuple(mount_offset)
        self.gem_offset = gem_offset
        self.az_offset = az_offset

        phi = math.radians(latitude)
        # unit vector along the polar axis, toward the visible celestial pole
        if phi >= 0:
            self._polar = (0.0, math.cos(phi), math.sin(phi))
        else:
            self._polar = (0.0, -math.cos(phi), -math.sin(phi))

    def dome_az(self, alt, az, side=None):
        """
        Dome azimuth (degrees) for a telescope pointing at alt, az (degrees).

        ``side`` is +1/-1 for the pier side (sign of the hour angle); when
        None it is inferred from the azimuth, which is exact except in the
        short post-transit window before an automatic pier flip.
        """
        alt_r = math.radians(alt)
        az_r = math.radians(az)
        p = (
            math.sin(az_r) * math.cos(alt_r),
            math.cos(az_r) * math.cos(alt_r),
            math.sin(alt_r),
        )

        if side is None:
            # sin(ha) = -sin(az)*cos(alt)/cos(dec), and cos(alt), cos(dec) > 0
            side = 1.0 if math.sin(az_r) <= 0 else -1.0

        a = self._polar
        d = (
            a[1] * p[2] - a[2] * p[1],
            a[2] * p[0] - a[0] * p[2],
            a[0] * p[1] - a[1] * p[0],
        )
        norm = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
        if norm < 1e-9:
            # pointing along the polar axis: the offset direction is
            # undetermined from alt/az alone, ignore the GEM offset
            offset = (0.0, 0.0, 0.0)
        else:
            k = side * self.gem_offset / norm
            offset = (k * d[0], k * d[1], k * d[2])

        m = self.mount_offset
        t0 = (m[0] + offset[0], m[1] + offset[1], m[2] + offset[2])

        tp = t0[0] * p[0] + t0[1] * p[1] + t0[2] * p[2]
        tt = t0[0] ** 2 + t0[1] ** 2 + t0[2] ** 2
        disc = tp**2 + 1.0 - tt
        if disc <= 0:
            raise ValueError(
                f"Tube position {t0} outside the dome: check the geometry parameters."
            )
        t = -tp + math.sqrt(disc)
        ix = t0[0] + t * p[0]
        iy = t0[1] + t * p[1]

        return (math.degrees(math.atan2(ix, iy)) + self.az_offset) % 360.0
