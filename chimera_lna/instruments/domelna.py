import os
import threading
import time
import math

from chimera.core import SYSTEM_CONFIG_DIRECTORY
from chimera.core.exceptions import ChimeraException, ObjectNotFoundException
from chimera.core.lock import lock
from chimera.interfaces.dome import InvalidDomePositionException, DomeStatus, Style
from chimera.util.coord import Coord, CoordUtil

from chimera_lna.util.dome_offset import CalcDomeAz

__author__ = 'william'

import serial
from chimera.instruments.dome import DomeBase


class DomeSlewTimeoutException(ChimeraException):
    '''
    Raised when dome times out when slewing.
    '''


class DomeLNA(DomeBase):
    def __init__(self):
        DomeBase.__init__(self)

        # Model, name, etc...
        self["model"] = "COTE/LNA custom dome"
        self["style"] = Style.Classic
        # self["park_position"] = 108
        self._park_tag = 900
        self["az_resolution"] = 2  # Will not move if (delta az) < 5 deg

        # Dome offsets. See description on util.dome_offset.
        self._offset_x = 0
        self._offset_y = 0
        self._offset_z = 0
        self._R = 147
        self._r = 49.2

        # Serial port
        self._serial = None

        # Few parameters...
        self._init_az = 108
        self._serial_timeout = 10  # seconds
        self._slitOpen = False  # FIXME: Slit open/closed should come from the dome.

        # Error handling constants
        self._dome_precision = 2  # Number of tags = +/- 4 degrees
        self._restart_precision = 4  # Number of tags = +/- 8 degrees.

        # Debug file
        self._debugLog = None
        try:
            self._debugLog = open(os.path.join(SYSTEM_CONFIG_DIRECTORY, "dome-debug.log"), "w")
        except IOError, e:
            self.log.warning("Could not create dome debug file (%s)" % str(e))

    def __start__(self):
        self._open()
        self._site = self.getSite()
        return super(DomeLNA, self).__start__()

    def _open(self):
        # Open the serial Port.
        self._serial = serial.Serial(self["device"], baudrate=9600, timeout=self._serial_timeout)
        # Reset the queue.
        self._command("MEADE PROG PARAR")
        time.sleep(1)
        # Restart controller
        self._command("MEADE PROG RESET")
        time.sleep(1)
        # Get the dome azimuth just to move it to the init position if needed.
        self.getAz()
        # # Check if connection is okay.
        # self._checkIdle()

    def _checkIdle(self):
        ack = self._command("MEADE PROG STATUS")
        if not ack:
            return False
        if ack[16] == '1':  # if '1', system busy
            return False
        else:
            return True

    def __stop__(self):
        # self._command("MEADE DOMO MOVER = %03d" % self._park_tag)
        self._close()

    def _close(self):
        if self._serial.isOpen():
            self._serial.close()

    def _debug(self, msg):
        if self._debugLog:
            print >> self._debugLog, time.time(), threading.currentThread().getName(), msg
            self._debugLog.flush()

    def _command(self, cmd):
        self._debug("[write] '%s'" % cmd)
        self._serial.write('%s\r' % cmd)
        t0 = time.time()
        ack = ''
        while '\r' not in ack:
            ack += self._serial.read()
            time.sleep(.1)
            if (time.time() - t0) > self._serial_timeout:
                self.log.debug('Error reading serial... Trying to flush it.')
                self._serial.readline()
                return False
        self._debug("[read ] '%s'" % ack)
        return ack.replace('\r', '')

    # utilitaries
    def getSite(self):
        try:
            p = self.getManager().getProxy('/Site/0', lazy=True)
            if not p.ping():
                return False
            else:
                return p
        except ObjectNotFoundException:
            return False

    @lock
    def lightsOn(self):
        return 'ACK' in self._command("MEADE FLAT_WEAK LIGAR")

    @lock
    def lightsOff(self):
        return 'ACK' in self._command("MEADE FLAT_WEAK DESLIGAR")

    def isSlitOpen(self):
        # FIXME: bool(self._command("MEADE PROG STATUS")[19])
        return self._slitOpen

    @lock
    def openSlit(self):
        ack = 'ACK' in self._command("MEADE TRAPEIRA ABRIR")
        if ack:
            self._slitOpen = True
        return ack

    @lock
    def closeSlit(self):
        return 'ACK' in self._command("MEADE TRAPEIRA FECHAR")

    def _getTag(self):
        ack = self._command("MEADE PROG STATUS")[8:11]

        if ack == '   ':
            self.log.info("Initializing dome...")
            self._init_dome()
            time.sleep(1)
            self.log.info("Dome initialized.")
            ack = float(self._command("MEADE PROG STATUS")[8:11])
        else:

            ack = float(ack)
        return ack

    @lock
    def getAz(self):

        ack = self._getTag()

        if ack < 846:  # 270 to 360 deg
            az = 270 + (ack - 801) * 2
        else:  # 0 to 270 deg
            az = (ack - 846) * 2

        return Coord.fromD(az)

    def _init_dome(self):
        self.slewToAz(self._init_az)

    @lock
    def slewToAz(self, az):

        # Dome is formed of tags with numbers from 801 to 982 (0 to 360 degress) where 801 is placed in the degree 270.
        if az > 360:
            raise InvalidDomePositionException("Cannot slew to %s. Outside azimuth limits." % az)

        # Stop any running tasks on dome controller.
        self._command('MEADE PROG PARAR')
        time.sleep(.5)

        # Calculate the dome azimuth offset.
        tel = self.getTelescope()
        site = self.getSite()
        if not tel or not site:
            self.log.error("I need a telescope and a site to calculate the dome offsets!")
        else:
            ha, dec, phi, x, y, z, r, R = CoordUtil.raToHa(tel.getRa().R, site.LST_inRads()).R, tel.getDec().R, \
                                          site["latitude"].R, self._offset_x, self._offset_y, self._offset_z, \
                                          self._r, self._R

            if ha > 2 * math.pi:
                ha -= 2 * math.pi
            elif ha < 2 * math.pi:
                ha += 2 * math.pi

            self.log.debug('ra, lst: %f, %f' % (tel.getRa().R, site.LST_inRads()))
            self.log.debug('Calculating offset for: ha, dec, phi, x, y, z, r, R = %f, %f, %f, %f, %f, %f, %f, %f' %
                           (ha, dec, phi, x, y, z, r, R))

            az1 = CalcDomeAz(ha, dec, phi, x, y, z, r, R)
            az1 *= (180 / math.pi)
            self.log.debug(
                "CalcDomeAz: Applying dome/telescope offset. Telescope az: %3.2f, dome az: %3.2f" % (az, az1))

            r = -r

            self.log.debug('Calculating offset for: ha, dec, phi, x, y, z, r, R = %f, %f, %f, %f, %f, %f, %f, %f' %
                           (ha, dec, phi, x, y, z, r, R))

            az1 = CalcDomeAz(ha, dec, phi, x, y, z, r, R)
            az1 *= (180 / math.pi)
            self.log.debug(
                "CalcDomeAz: Applying dome/telescope offset. Telescope az: %3.2f, dome az: %3.2f" % (az, az1))

        if az >= 270:
            dome_tag = int(math.ceil((az - 270) / 2. + 801))
        else:
            dome_tag = int(math.ceil(az / 2. + 846))

            # TODO: Check if is slewing. Add abort point?
            # # ok, we are slewing now
            # self._slewing = True
        if 'ACK' in self._command("MEADE DOMO MOVER = %03d" % dome_tag):
            self.slewBegin(az)
        else:
            self.log.info('No ACK from dome when slewing.')
            return False
        time.sleep(0.6)
        t0 = time.time()
        while not self._checkIdle():
            if time.time() - t0 > self["slew_timeout"]:
                raise DomeSlewTimeoutException("Timeout moving the dome")
            time.sleep(1)

        # Check the final position.
        # If the position is too far from the desired (restart_precision),
        # try to restart the dome and put it on the correct position.
        for delta_tag in [10, 30, 90]:
            tag_now = self._getTag()
            if abs(tag_now - dome_tag) < self._restart_precision:
                # If position is wrong for less than restart_precision, just confirm.
                self.slewComplete(self.getAz(), DomeStatus.OK)
                return True

            else:

                back_tag = dome_tag - delta_tag
                if back_tag < 801:
                    back_tag += 982
                self.log.debug('Dome position error >= %i. Moving dome back to %i. (in tag units)' %
                               (self._restart_precision, back_tag))
                # Move dome back
                self._command("MEADE DOMO MOVER = %03d" % back_tag)
                time.sleep(0.6)
                # Wait for movement
                t0 = time.time()
                while not self._checkIdle():
                    if time.time() - t0 > self["slew_timeout"]:
                        raise DomeSlewTimeoutException("Timeout moving the dome")
                    time.sleep(1)
                # Move dome forward
                self.log.debug('Moving dome forward again.')
                self._command("MEADE DOMO MOVER = %03d" % dome_tag)
                time.sleep(0.6)
                # Wait for movement
                t0 = time.time()
                while not self._checkIdle():
                    if time.time() - t0 > self["slew_timeout"]:
                        raise DomeSlewTimeoutException("Timeout moving the dome")
                    time.sleep(1)

    def abortSlew(self):
        return NotImplementedError()

    def isSlewing(self):
        return not self._checkIdle()


if __name__ == '__main__':
    d = DomeLNA()
    d["device"] = "COM3"
    d._open()
    time.sleep(2)
    for c in ["MEADE PROG PARAR", "MEADE PROG RESET", "MEADE PROG STATUS"]:
        print "running '%s' command on dome" % c
        print d._command(c)
        time.sleep(2)
    print("Checking dome status: (False = idle, True = not idle)")
    print(d._checkIdle())
    # d._close()
    print 'Finished.'


    # for i in range(950, 982):
    #     print "Moving to %d" % i
    #     ack = d._command('MEADE DOMO MOVER = %d' % i)
    #     if ack == 'NAK':
    #         print '%d: NAK' % i
    #         break
    #     time.sleep(.5)
    #     t0 = time.time()
    #     while not d._checkIdle():
    #         if time.time() - t0 > d["slew_timeout"]:
    #             raise DomeSlewTimeoutException("Timeout moving the dome")
    #         time.sleep(1)
    #     ack = d._command("MEADE PROG STATUS")
    #     print '%d, %s' % (i, ack[8:11])
    #     time.sleep(5)
    # raw_input("ENTER for next position...")
