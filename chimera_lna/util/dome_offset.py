from __future__ import division
import numpy as np

# At LNA:
#  dec_length: 49.2
#  dome_radius: 147
# r = 33 * np.cos((22+29)*np.pi/180.) -10  = 10.77 cm - From Paramount ME Manual
# R = 147 cm - LNA dome.

class CalcDomeException(Exception):
    pass

def CalcDomeAz(ha, dec, phi, X, Y, Z, r, R):
    """
    Calculates the corrected Azimuth in degrees given the dome/telescope geometric locations.

    :param ha: Telescope hour-angle in radians
    :param dec: Telescope declination in radians
    :param phi: Telescope elevation of the polar axis (usually the site latitude) in radians
    :param X: Telescope gravity center position X. See dome_syncronization.pdf on documentation.
    :param Y: Telescope gravity center position Y. See dome_syncronization.pdf on documentation.
    :param Z: Telescope gravity center position Z. See dome_syncronization.pdf on documentation.
    :param r: Distance along the declination axis from the Gravity Center to the optical axis
    :param R: Dome radius
    :return dome_az: Corrected dome Azimuth in radians.
    """

    print ('ha, dec, phi, X, Y, Z, r, R:', ha, dec, phi, X, Y, Z, r, R)

    # Calculate position of the optical axis origin with respect to dome center.

    x = X+r*np.cos(phi)*np.cos(ha)
    y = Y-r*np.sin(phi)*np.sin(ha)
    z = Z-r*np.cos(phi)*np.sin(ha)

    # unit vector with the direction of the optical axis (to the object)
    obsPoleSign = 1. if phi > 0. else -1. # 1. if observatory is in the North pole, -1. if South
    dang = (np.pi/2.+(obsPoleSign*dec))-(obsPoleSign*phi)

    vx = np.sin(ha)*np.sin(dang*np.pi/180.)
    vy = np.cos(dang*np.pi/180.)+np.zeros_like(ha)
    vz = np.cos(ha)*np.sin(dang*np.pi/180.)-np.cos(dang*np.pi/180.)

    A = np.linspace(R/2.,R*2.,100)
    res = (x+A*vx)**2.+(y+A*vy)**2.+(z+A*vz)**2. - R**2.
    Av = A[res.argmin()]

    v = np.arccos((z+Av*vz)/R)
    ux = np.arccos((x+Av*vx)/(R*np.sin(v)))
    dy = y+Av*vy

    if dy < 0.:
        ux = 2.*np.pi-ux

    return ux