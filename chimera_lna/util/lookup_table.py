import os

import numpy as np
from chimera.util.coord import Coord
from chimera.util.position import Position

import chimera_lna


class DomeLookupTable:
    def __init__(self):
        self._table = np.loadtxt(
            f"{os.path.dirname(chimera_lna.__file__)}/data/dome_model.csv",
            delimiter=",",
        )
        self._coordinates = [
            [Position.from_alt_az(Coord.from_r(v[0]), Coord.from_r(v[1])), v[2]]
            for v in self._table
        ]

    def get_tag_altaz(self, alt: float, az: float, ret_distance=False):
        """
        Returns the nearest tag for a given position: alt, az in degrees.
        If ret_distance is `True`, returns distance from lookuptable value to the point.
        """
        position = Position.from_alt_az(Coord.from_r(alt), Coord.from_r(az))
        argmin = np.argmin([v[0].angsep(position) for v in self._coordinates])
        if ret_distance:
            return int(self._coordinates[argmin][1]), self._coordinates[argmin][
                0
            ].angsep(position)
        else:
            return int(self._coordinates[argmin][1])


if __name__ == "__main__":
    dl = DomeLookupTable()
    for c in [[25, 25], [88, 123], [25, 30]]:
        print(
            "alt, az, (tag, distance): ",
            c[0],
            c[1],
            dl.get_tag_altaz(Position.from_alt_az(c[0], c[1]), ret_distance=True),
        )
