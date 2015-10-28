import sys

from chimera.core.manager import Manager
from chimera.util.coord import CoordUtil
from chimera.util.position import Position, Epoch

if len(sys.argv) < 2:
    print 'Usage: %s ip_chimera' % sys.argv[0]
    sys.exit()

manager = Manager()

site = manager.getProxy("%s:7666/Site/0" % sys.argv[1])
telescope = manager.getProxy("%s:7666/Telescope/0" % sys.argv[1])
dome = manager.getProxy("%s:7666/Dome/0" % sys.argv[1])

raw_input('Center the telescope on the dome slit and press ENTER to start recording...')

model_file = open('dome_model.csv', 'w')

model_file.write('Latitude, %s\n' % site["latitude"].R)

model_file.write('Dome Azm, Scope Ha, Scope Dec, Pier Side\n')

try:
    while True:
        az, ra, dec = dome.getAz().D, telescope.getRa().R, telescope.getDec().R
        # x = Position.fromRaDec(ra, dec, epoch=Epoch.J2000)
        # x = x.toEpoch(Epoch.NOW)
        # ra = x.ra.R
        # dec = x.dec.R
        lst = site.LST()
        ha = CoordUtil.raToHa(ra, lst).R
        print 'Dome Azm, Scope Ha, Scope Dec, Site LST:', az, ha, dec, lst
        model_file.write('%f,%f,%f,pierWest\n' % (az, ha, dec))
        raw_input('Move the telescope, re-center, and press ENTER to grab. CRTL+C to finish.')
except KeyboardInterrupt:
    model_file.close()
    print '\nDone'

# Generate the file like:
# Latitude, 51,07861
# Dome Azm, Scope Ha, Scope Dec, Pier Side
# 180,-4,44089209850063E-16,-3,18055468146352E-15,pierWest
# 180,-4,44089209850063E-16,-3,18055468146352E-15,pierEast
# 180,-4,44089209850063E-16,-3,18055468146352E-15,pierEast
