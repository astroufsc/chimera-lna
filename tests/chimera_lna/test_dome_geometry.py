# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later

import os

import numpy as np
import pytest

import chimera_lna
from chimera_lna.util.dome_geometry import DomeGeometry


def wrap(x):
    return (x + 180) % 360 - 180


@pytest.fixture(scope="module")
def mapping_run():
    """The 2016 dome mapping run the default parameters were fitted to."""
    raw = np.genfromtxt(
        os.path.join(os.path.dirname(chimera_lna.__file__), "data", "model.csv"),
        delimiter=",",
        names=True,
        dtype=None,
        encoding="utf-8",
        invalid_raise=False,
    )
    alt = np.asarray(raw["ALT"], float)
    az = np.asarray(raw["AZ"], float)
    tag = np.asarray(raw["ETIQUETA"], float)
    good = ~((alt == 0) & (az == 0) & (tag == 0)) & np.isfinite(alt) & np.isfinite(tag)
    dome_az = np.where(tag < 846, 270 + (tag - 801) * 2, (tag - 846) * 2) % 360
    return np.degrees(alt[good]), np.degrees(az[good]), dome_az[good]


class TestDomeGeometry:
    def test_reproduces_the_mapping_run(self, mapping_run):
        # the model must agree with the measured table to within the 2 deg
        # tag quantization; the metric that matters physically is the miss
        # transverse to the slit, azimuth error shrunk by cos(elevation)
        alt, az, dome_az = mapping_run
        geometry = DomeGeometry()
        residual = np.array(
            [wrap(geometry.dome_az(a, z) - o) for a, z, o in zip(alt, az, dome_az)]
        )
        elev = 90.0 - np.degrees(
            np.arccos(np.clip(np.sin(np.radians(alt)), -1, 1))
        )  # dome elevation ~ alt near the sphere; good enough to de-weight
        slit_miss = residual * np.cos(np.radians(np.minimum(elev, 89.0)))
        assert np.median(np.abs(residual)) < 2.5
        assert np.median(np.abs(slit_miss)) < 2.0
        # a small tail is expected: the run tracked past the meridian on a
        # few points (wrong inferred pier side) and has one corrupt row
        assert np.mean(np.abs(slit_miss) < 8.0) > 0.9

    def test_beats_the_naive_on_axis_model(self, mapping_run):
        alt, az, dome_az = mapping_run
        geometry = DomeGeometry()
        model = np.array(
            [wrap(geometry.dome_az(a, z) - o) for a, z, o in zip(alt, az, dome_az)]
        )
        naive = wrap(az - dome_az)
        assert np.median(np.abs(model)) < 0.2 * np.median(np.abs(naive))

    def test_zenith_is_defined_and_continuous(self):
        geometry = DomeGeometry()
        for az in range(0, 360, 15):
            value = geometry.dome_az(89.5, az)
            assert 0.0 <= value < 360.0
        # on a fixed pier side the answer is continuous through the zenith,
        # where the nearest-neighbor lookup table used to jump between
        # entries measured on opposite sides
        walk = [geometry.dome_az(alt, 90.0, side=-1) for alt in (88.0, 89.0, 89.9)]
        steps = [abs(wrap(b - a)) for a, b in zip(walk, walk[1:])]
        assert max(steps) < 10.0

    def test_horizon_is_defined(self):
        geometry = DomeGeometry()
        for az in range(0, 360, 15):
            value = geometry.dome_az(0.0, az)
            assert 0.0 <= value < 360.0

    def test_polar_axis_pointing_does_not_crash(self):
        # pointing along the polar axis the declination axis direction is
        # undefined: the model must fall back instead of dividing by zero
        geometry = DomeGeometry()
        value = geometry.dome_az(22.5344, 180.0)
        assert 0.0 <= value < 360.0

    def test_pier_side_changes_the_answer(self):
        geometry = DomeGeometry()
        east = geometry.dome_az(45.0, 90.0, side=-1)
        west = geometry.dome_az(45.0, 90.0, side=+1)
        assert abs(wrap(east - west)) > 5.0

    def test_side_inferred_from_azimuth(self):
        # east of the meridian (0 < az < 180) the hour angle is negative
        geometry = DomeGeometry()
        assert geometry.dome_az(45.0, 90.0) == geometry.dome_az(45.0, 90.0, side=-1)
        assert geometry.dome_az(45.0, 270.0) == geometry.dome_az(45.0, 270.0, side=+1)

    def test_tube_outside_dome_raises(self):
        geometry = DomeGeometry(mount_offset=(0.0, 0.0, 1.5), gem_offset=0.0)
        with pytest.raises(ValueError):
            geometry.dome_az(45.0, 90.0)
