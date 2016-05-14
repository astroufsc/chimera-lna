import sys

import datetime
from chimera.core.manager import Manager
from chimera.util.coord import CoordUtil, Coord
import numpy as np

import gspread
from chimera.util.position import Position
from oauth2client.service_account import ServiceAccountCredentials

scope = ['https://spreadsheets.google.com/feeds']

credentials = ServiceAccountCredentials.from_json_keyfile_name('LNA40-acf26ff37a3d.json', scope)

gc = gspread.authorize(credentials)

wks = gc.open_by_key('')

sheet = wks.get_worksheet(0)

if len(sys.argv) < 2:
    print 'Usage: %s ip_chimera' % sys.argv[0]
    sys.exit()

manager = Manager()

site = manager.getProxy("%s:7666/Site/0" % sys.argv[1])
telescope = manager.getProxy("%s:7666/Telescope/0" % sys.argv[1])
dome = manager.getProxy("%s:7666/Dome/0" % sys.argv[1])


make_alt_list = False
if make_alt_list:
    i = 1
    while sheet.cell(i, 1).value != '':
        i += 1
    for az, alt in [(ii, jj) for ii in np.arange(15, 360, 10) for jj in np.arange(25, 90, 20)]:
        sheet.update_cell(i, 1, az)
        sheet.update_cell(i, 2, alt)
        i += 1


# raw_input('Center the telescope on the dome slit and press ENTER to start recording...')

# model_file = open('dome_model.csv', 'w')
#
# model_file.write('Latitude, %s\n' % site["latitude"].R)
#
# model_file.write('Dome Azm, Scope Ha, Scope Dec, Pier Side\n')
i = 1

try:
    # for az, alt in [(ii, jj) for ii in np.arange(15, 360, 10) for jj in np.arange(25, 90, 20)]:
    while sheet.cell(i, 1).value != '':
        if sheet.cell(i, 9).value == '':
            az, alt = float(sheet.cell(i, 1).value), float(sheet.cell(i, 2).value)
            print '@> alt, az', alt, az
            telescope.slewToAltAz(Position.fromAltAz(Coord.fromD(alt), Coord.fromD(az)))
            etiqueta = raw_input('Etiqueta: ')
            alt, az, ra, dec = telescope.getAlt().R, telescope.getAz().R, telescope.getRa().R, telescope.getDec().R
            lst = site.LST()
            ha = CoordUtil.raToHa(ra, lst).R
            j = 3
            for val in (alt, az, ha, ra, dec, lst, etiqueta, str(datetime.datetime.now())):
                sheet.update_cell(i, j, val) #model_file.write('%f,%f,%f,pierWest\n' % (az, ha, dec))
                j += 1
            # i += 1
            # raw_input('Move the telescope, re-center, and press ENTER to grab. CRTL+C to finish.')
        i += 1
except KeyboardInterrupt:
    # model_file.close()
    print '\nDone'

# Generate the file like:
# Latitude, 51,07861
# Dome Azm, Scope Ha, Scope Dec, Pier Side
# 180,-4,44089209850063E-16,-3,18055468146352E-15,pierWest
# 180,-4,44089209850063E-16,-3,18055468146352E-15,pierEast
# 180,-4,44089209850063E-16,-3,18055468146352E-15,pierEast
