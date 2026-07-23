# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later

import numpy as np

from chimera_lna.util.lookup_table import DomeLookupTable


class TestDomeLookupTable:
    def test_returns_known_tag_for_table_entry(self):
        lookup = DomeLookupTable()
        # pick an entry straight from the table: the nearest tag for its own
        # coordinates must be itself, at zero distance.
        alt = np.degrees(lookup._alt[0])
        az = np.degrees(lookup._az[0])
        tag, distance = lookup.get_tag_altaz(alt, az, ret_distance=True)
        assert tag == lookup._tags[0]
        assert distance < 1e-6

    def test_tag_in_valid_range(self):
        lookup = DomeLookupTable()
        for alt, az in [(25, 25), (88, 123), (25, 30), (45, 350)]:
            tag = lookup.get_tag_altaz(alt, az)
            assert isinstance(tag, int)
            assert 801 <= tag <= 982

    def test_distance_returned_in_degrees(self):
        lookup = DomeLookupTable()
        tag, distance = lookup.get_tag_altaz(45, 180, ret_distance=True)
        assert 0.0 <= distance <= 180.0
