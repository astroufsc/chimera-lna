#! /usr/bin/env python
# -*- coding: iso-8859-1 -*-

# chimera - observatory automation system
# Copyright (C) 2006-2007  P. Henrique Silva <henrique@astro.ufsc.br>

# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

import time
import threading
import math
import os
import select

import serial

from chimera.instruments.dome import DomeBase
from chimera.interfaces.dome import InvalidDomePositionException, DomeStatus
from chimera.core.exceptions import ChimeraException
from chimera.core.lock import lock
from chimera.core.constants import SYSTEM_CONFIG_DIRECTORY
from chimera.util.coord import Coord


class DomeLNA40cm(DomeBase):
    def __init__(self):  # Initializations
        DomeBase.__init__(self)

        self.tty = None
        self.abort = threading.Event()

        self._slewing = False
        self._slitOpen = False

        self._az_shift = 0

        self._num_restarts = 0
        self._max_restarts = 3

        self._max_status_tries = 60  # ~ 6 seconds

        # park position
        self["park_position"] = 106  # degrees

        # debug log
        self._debugLog = None
        try:
            self._debugLog = open(os.path.join(SYSTEM_CONFIG_DIRECTORY, "dome-debug.log"), "w")
        except IOError, e:
            self.log.warning("Could not create meade debug file (%s)" % str(e))

    def __start__(self):

        # NOTE: DomeBase __start__ expect the serial port to be open, so open it before
        # calling super().__start__.

        try:
            self.open()
        except Exception, e:
            self.log.exception(e)
            return False

        return super(DomeLNA40cm, self).__start__()

    def __stop__(self):

        # NOTE: Here is the opposite, call super first and then close

        ret = super(DomeLNA40cm, self).__stop__()
        if not ret:
            return ret

        self.close()

        return True

    def _checkIdle(self):

        self.tty.setTimeout(0.3)

        n_tries = 1
        while True:
            self._write("MEADE PROG STATUS")  # Ask for system status
            time.sleep(0.5)
            ack = self._readline()
            self.log.debug("Dome ack: '%s'" % ack)
            if ack.startswith("    "):
                if ack[16] == '1':  # if '1', system busy
                    self.log.debug("Dome is busy.")
                    return False
                else:
                    return True
            elif n_tries > self._max_status_tries:
                self.log.error("Error reading dome status, restarting Dome.")
                self._restartDome()
                return False
            self.log.debug("Dome not ready. Try %02d" % n_tries)
            n_tries += 1
            time.sleep(0.5)

        if ack[16] == '1':
            return False

        return True

    def _restartDome(self):

        if self._num_restarts >= self._max_restarts:
            raise ChimeraException(
                "Could not restart the dome after %s tries. Manual restart needed." % self._max_restarts)
        else:
            self._num_restarts += 1

        self.log.info("Trying to restart the Dome.")

        self._write("MEADE PROG PARAR")
        self._readline()
        time.sleep(1)

        self._write("MEADE PROG RESET")  # Restart Command

        ack = self._readline()
        if not ack == "ACK":
            if not self._checkIdle():
                raise ChimeraException("Could not restart dome! Manual restart needed.")
            else:
                self._num_restarts = 0
                return True

        self._num_restarts = 0

        return True

    @lock
    def open(self):

        self.tty = serial.Serial(self["device"], baudrate=9600,
                                 bytesize=serial.EIGHTBITS,
                                 parity=serial.PARITY_NONE,
                                 stopbits=serial.STOPBITS_ONE,
                                 timeout=self["init_timeout"],
                                 xonxoff=1, rtscts=0)

        self.tty.flushInput()
        self.tty.flushOutput()

        self._restartDome()

        self._checkIdle()

    @lock
    def close(self):
        if self.tty.isOpen():
            self.tty.close()

    @lock
    def slewToAz(self, az):

        i = 0
        while not self._checkIdle():  # Verify if dome is idle
            i += 1
            if i < self._max_status_tries:
                self.log.warning("Error, Dome busy. Try %02i." % i)
                time.sleep(1)
            else:
                self._restartDome()
                return

        if not isinstance(az, Coord):
            az = Coord.fromDMS(az)

        # correct dome/telescope phase difference
        dome_az = az.D + self._az_shift

        if dome_az > 360:
            raise InvalidDomePositionException("Cannot slew to %s. "
                                               "Outside azimuth limits." % az)

        if dome_az >= 270:
            dome_az = int(math.ceil(dome_az / self["az_resolution"]))
            dome_az += 666  # Values between 801 and 982, starting in 207 degrees
        else:
            dome_az = int(math.ceil(dome_az / self["az_resolution"]))
            dome_az += 847

        pstn = "MEADE DOMO MOVER = %03d" % dome_az

        i = 0
        while i < self._max_status_tries:
            i += 1
            self._write(pstn)
            time.sleep(1)
            ack = self._readline()

            if not ack.startswith("ACK"):
                self.log.error("Error trying to slew the dome to azimuth '%s' (dome azimuth '%s'). Try: %02i" % (az, dome_az, i))
                if i == self._max_status_tries:
                    self._restartDome()
                    raise ChimeraException("Error trying to slew the dome to azimuth '%s' (dome azimuth '%s'). Dome restarted." % (az, dome_az))
            else:
                return

            # ok, we are slewing now
            self._slewing = True
            self.slewBegin(az)

        # FIXME: add abort option here

        for x in range(200):  # Total of 60 seconds waiting
            time.sleep(0.5)
            if self._checkIdle():
                self._slewing = False
                self.slewComplete(self.getAz(), DomeStatus.OK)
                # break
                return

        if not self._checkIdle():
            self.log.warning("Error, restarting Dome.")
            self._restartDome()

    def isSlewing(self):
        return self._slewing

    def abortSlew(self):
        # FIXME: make abort work

        if not self.isSlewing(): return

        self._write("MEADE PROG PARAR")

        self.tty.setTimeout(self["abort_timeout"])
        ack = self._readline()

        if ack != "ACK":
            raise IOError("Error while trying to stop the dome.")

    @lock
    def getAz(self):

        if not self._checkIdle():  # Verify if dome is idle
            self.log.warning("Error, Dome busy.")
            return

        cmd = "MEADE PROG STATUS"

        self._write(cmd)

        ack = self._readline()

        # check timeout
        if not ack:
            self.log.warning("Dome timeout, restarting it.")
            self._restartDome()
            return self.getAz()
            # raise IOError("Couldn't get azimuth after %d seconds." % 10)

        # get ack return
        if ack.startswith("    "):
            ack = ack[8:11]

        if ack == "   ":
            self.log.warning("No information on dome position. Move dome to initialize positioning")
            ack = "000"

        # correct dome/telescope phase difference
        az = int(ack)
        if az >= 847:  # Values between 801 and 982, starting in 207 degrees
            az -= 847
        else:
            az -= 666
        az = int(math.ceil(az * self["az_resolution"]))
        az -= self._az_shift
        az %= 360

        return Coord.fromDMS(az)

    @lock
    def openSlit(self):

        if not self._checkIdle():  # Verify if dome is idle
            self.log.warning("Error, Dome busy.")
            return

        cmd = "MEADE TRAPEIRA ABRIR"

        self._write(cmd)

        ack = self._readline()

        if ack != "ACK":
            raise IOError("Error trying to open the slit.")

        for x in range(30):  # Tries 3 seconds
            if self._checkIdle():
                self._slitOpen = True
                # self.slitOpened(self.getAz())
                return
            time.sleep(1.5)

        if not self._checkIdle():
            self.log.warning("Error, restarting Dome.")
            self._restartDome()

    @lock
    def closeSlit(self):

        if not self._checkIdle():  # Verify if dome is idle
            self.log.warning("Error, Dome busy.")
            return

        cmd = "MEADE TRAPEIRA FECHAR"

        self._write(cmd)

        ack = self._readline()

        if ack != "ACK":
            raise IOError("Error trying to close the slit.")

        for x in range(30):  # Tries 30 seconds
            if self._checkIdle():
                self._slitOpen = False
                # self.slitClosed(self.getAz())
                return
            time.sleep(1.5)

        if not self._checkIdle():
            self.log.warning("Error, restarting Dome.")
            self._restartDome()

    def isSlitOpen(self):
        return self._slitOpen

    @lock
    def lightsOn(self):

        if not self._checkIdle():  # Verify if dome is idle
            self.log.warning("Error, Dome busy.")
            return

        cmd = "MEADE FLAT_WEAK LIGAR"

        self._write(cmd)

        fin = self._readline()

        if fin != "ACK":
            raise IOError("Error trying to turn lights on.")

    @lock
    def lightsOff(self):

        if not self._checkIdle():  # Verify if dome is idle
            self.log.warning("Error, Dome busy.")
            return

        cmd = "MEADE FLAT_WEAK DESLIGAR"

        self._write(cmd)

        fin = self._readline()

        if fin != "ACK":
            raise IOError("Error trying to turn lights off.")

    #
    # low level
    #

    def _debug(self, msg):
        if self._debugLog:
            print >> self._debugLog, time.time(), threading.currentThread().getName(), msg
            self._debugLog.flush()

    def _read(self, n=1):
        if not self.tty.isOpen():
            raise IOError("Device not open")

        self.tty.flushInput()

        return self.tty.read(n)

    def _readline(self):
        if not self.tty.isOpen():
            raise IOError("Device not open")

        # self.tty.flushInput()

        try:
            ret = self.tty.readline()  #None, eol)
        except select.error:
            ret = self.tty.readline()  #None, eol)

        self._debug("[read ] '%s'" % repr(ret).replace("'", ""))

        if ret:
            # remove eol marks
            return ret[:-1]
        else:
            return ""

    def _readbool(self, n=1):
        ret = int(self._read(1))

        if not ret:
            return False

        return True

    def _write(self, data, eol="\r"):
        if not self.tty.isOpen():
            raise IOError("Device not open")

        self.tty.flushOutput()

        time.sleep(1.5)

        self._debug("[write] '%s%s'" % (repr(data).replace("'", ""), repr(eol).replace("'", "")))
        ret = self.tty.write("%s%s" % (data, eol))
        return ret
