# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dome tag lookup table for the LNA dome."""

import os

import numpy as np

import chimera_lna


class DomeLookupTable:
    """
    Maps telescope (alt, az) positions to the nearest dome tag using the
    empirical dome model table (data/dome_model.csv: alt[rad], az[rad], tag).
    """

    def __init__(self):
        table = np.loadtxt(
            os.path.join(
                os.path.dirname(chimera_lna.__file__), "data", "dome_model.csv"
            ),
            delimiter=",",
        )
        self._alt = table[:, 0]  # radians
        self._az = table[:, 1]  # radians
        self._tags = table[:, 2].astype(int)

    def _angular_separation(self, alt, az):
        """
        Angular separation (radians) between (alt, az) [radians] and every
        entry of the table, using the spherical law of cosines.
        """
        cos_sep = np.sin(self._alt) * np.sin(alt) + np.cos(self._alt) * np.cos(
            alt
        ) * np.cos(self._az - az)
        return np.arccos(np.clip(cos_sep, -1.0, 1.0))

    def get_tag_altaz(self, alt: float, az: float, ret_distance=False):
        """
        Returns the nearest tag for a given position: alt, az in degrees.
        If ret_distance is `True`, also returns the angular distance (degrees)
        from the lookup table entry to the point.
        """
        separation = self._angular_separation(np.radians(alt), np.radians(az))
        argmin = int(np.argmin(separation))
        if ret_distance:
            return int(self._tags[argmin]), float(np.degrees(separation[argmin]))
        return int(self._tags[argmin])


if __name__ == "__main__":
    lookup_table = DomeLookupTable()
    for alt, az in [(25, 25), (88, 123), (25, 30)]:
        print(
            "alt, az, (tag, distance): ",
            alt,
            az,
            lookup_table.get_tag_altaz(alt, az, ret_distance=True),
        )
