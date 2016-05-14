import os

import numpy as np
from chimera.util.coord import Coord
from chimera.util.position import Position

import chimera_lna


class DomeLookupTable(object):
    def __init__(self):
        self._table = np.loadtxt('%s/data/dome_model.csv' % os.path.dirname(chimera_lna.__file__), delimiter=',')
        self._coordinates = [[Position.fromAltAz(Coord.fromR(v[0]), Coord.fromR(v[1])), v[2]] for v in self._table]

    def get_tag_altaz(self, position, ret_distance=False):
        """
        Returns the nearest tag for a given position (AltAz).
        If ret_distance is `True`, returns distance from lookuptable value to the point.
        """
        argmin = np.argmin([v[0].angsep(position) for v in self._coordinates])
        if ret_distance:
            return int(self._coordinates[argmin][1]), self._coordinates[argmin][0].angsep(position)
        else:
            return int(self._coordinates[argmin][1])


if __name__ == '__main__':
    dl = DomeLookupTable()
    for c in [[25, 25], [88, 123], [25, 30]]:
        print 'alt, az, (tag, distance): ', c[0], c[1], dl.get_tag_altaz(Position.fromAltAz(c[0], c[1]),
                                                                         ret_distance=True)
