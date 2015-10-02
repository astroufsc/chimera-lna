import os
import threading
from chimera.core import SYSTEM_CONFIG_DIRECTORY
import time
from chimera.core.exceptions import ChimeraException
from chimera.core.lock import lock
from chimera.interfaces.dome import InvalidDomePositionException, DomeStatus
import math
from chimera.util.coord import Coord

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

        # Serial port
        self._serial = None

        # Few parameters...
        self._init_az = 108
        self._serial_timeout = 5  # seconds
        self._slitOpen = False  # FIXME: Slit open/closed should come from the dome.

        # Debug file
        self._debugLog = None
        try:
            self._debugLog = open(os.path.join(SYSTEM_CONFIG_DIRECTORY, "dome-debug.log"), "w")
        except IOError, e:
            self.log.warning("Could not create meade debug file (%s)" % str(e))

    def __start__(self):
        self._open()
        # return super(DomeLNA, self).__start__()
        return True

    def _open(self):
        # Open the serial Port.
        self._serial = serial.Serial(self["device"], baudrate=9600, timeout=self._serial_timeout)
        # Reset the queue.
        self._command("MEADE PROG PARAR")
        # Get the dome azimuth just to move it to the init position if needed.
        self.getAz()
        # # Check if connection is okay.
        # self._checkIdle()

    def _checkIdle(self):
        ack = self._command("MEADE PROG STATUS")
        if ack[16] == '1':  # if '1', system busy
            return False
        else:
            return True

    def __stop__(self):
        self.slewToAz(self._init_az)
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
                return False
        self._debug("[read ] '%s'" % ack)
        return ack.replace('\r', '')

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

    @lock
    def getAz(self):
        ack = self._command("MEADE PROG STATUS")[8:11]

        if ack == '   ':
            self.log.info("Initializing dome...")
            self._init_dome()
            time.sleep(1)
            self.log.info("Dome initialized.")
            ack = float(self._command("MEADE PROG STATUS")[8:11])
        else:
            ack = float(ack)

        if ack < 846:  # 270 to 360 deg
            az = 270 + (ack-801)*2
        else:  # 0 to 270 deg
            az = (ack - 846)*2

        return Coord.fromD(az)

    def _init_dome(self):
        self.slewToAz(self._init_az)

    @lock
    def slewToAz(self, az):

        # Dome is formed of tags with numbers from 801 to 982 (0 to 360 degress) where 801 is placed in the degree 270.

        if az > 360:
            raise InvalidDomePositionException("Cannot slew to %s. Outside azimuth limits." % az)

        if az >= 270:
            dome_tag = int(math.ceil((az - 270) / 2. + 801))
        else:
            dome_tag = int(math.ceil(az / 2. + 846))

            # TODO: Check if is slewing. Add abort point?
            # # ok, we are slewing now
            # self._slewing = True
        if 'ACK' in self._command("MEADE DOMO MOVER = %03d" % dome_tag):
            self.slewBegin(az)

        t0 = time.time()
        while not self._checkIdle():
            if time.time() - t0 > self["slew_timeout"]:
                raise DomeSlewTimeoutException("Timeout moving the dome")

        self.slewComplete(self.getAz(), DomeStatus.OK)

        return True

    def abortSlew(self):
        return NotImplementedError

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
    d._close()
    print 'Finished.'
