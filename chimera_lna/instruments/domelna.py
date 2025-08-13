import math
import os
import threading
import time

from chimera.core import SYSTEM_CONFIG_DIRECTORY
from chimera.core.exceptions import ChimeraException
from chimera.core.lock import lock
from chimera.instruments.lamp import LampBase
from chimera.interfaces.dome import DomeStatus, InvalidDomePositionException, Style
from chimera.util.coord import Coord

from chimera_lna.util.lookup_table import DomeLookupTable
from chimera.instruments.dome import DomeBase

import serial

class DomeSlewTimeoutException(ChimeraException):
    """
    Raised when dome times out when slewing.
    """


class DomeLNA(DomeBase, LampBase):
    def __init__(self):
        DomeBase.__init__(self)
        LampBase.__init__(self)

        # Model, name, etc...
        self._light_on = False
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
        self._restart_tries = 3

        self._initalizing = False

        # Load LookUp table
        self._lookup = DomeLookupTable()

        # Debug file
        self._debugLog = None
        try:
            self._debugLog = open(
                os.path.join(SYSTEM_CONFIG_DIRECTORY, "dome-debug.log"), "w"
            )
        except OSError as e:
            self.log.warning(f"Could not create dome debug file ({str(e)})")

    def __start__(self):
        self._open()
        return super().__start__()

    def _open(self):
        # Open the serial Port.
        self._serial = serial.Serial(
            self["device"], baudrate=9600, timeout=self._serial_timeout
        )
        # On start, reset the dome.
        self._resetDome(reset_tag=900)
        # Get the dome azimuth just to move it to the init position if needed.
        self.get_az()
        # # Check if connection is okay.
        self._checkIdle()

    def _resetDome(self, reset_tag=None):
        self._serial.flushInput()
        self._serial.flushOutput()
        ack = ""
        # Reset the queue.
        for i in range(self._restart_tries):
            if not ack.startswith("ACK"):
                ack = self._command("MEADE PROG PARAR")
                time.sleep(2)
        # Restart controller
        ack = ""
        for i in range(self._restart_tries):
            if not ack.startswith("ACK"):
                ack = self._command("MEADE PROG RESET")
                time.sleep(2)

        if reset_tag is not None:
            # When resetting the dome, move it to the reset_tag
            ack = self._command("MEADE DOMO MOVER = %03d" % reset_tag)
            if not ack.startswith("ACK"):
                ack = self._command("MEADE DOMO MOVER = %03d" % reset_tag)
                time.sleep(2)

            # Try to move the dome again
            t0 = time.time()
            while not self._checkIdle():
                if time.time() - t0 > self["slew_timeout"]:
                    self.log.debug("Timeout moving the dome")
                    return
                time.sleep(1)
        else:
            return

    def _checkIdle(self):
        ack = self._command("MEADE PROG STATUS")
        if ack.startswith("NAK"):
            self.log.debug("Got a NAK on status.")
            # self._resetDome()
            return False
        if len(ack) < 17:  # Sometimes ack is shit. So, return that dome is busy.
            return False
        if ack[16] == "1":  # if '1', system busy
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
            print(
                time.time(),
                threading.currentThread().getName(),
                msg,
                file=self._debugLog,
            )
            self._debugLog.flush()

    def _command(self, cmd):
        self._serial.flushOutput()
        self._serial.flushInput()
        self._debug(f"[write] '{cmd}'")
        self._serial.write(f"{cmd}\r".encode())
        t0 = time.time()
        ack = ""
        while "\r" not in ack:
            ack += self._serial.read().decode()
            time.sleep(0.1)
            if (time.time() - t0) > self._serial_timeout:
                self.log.debug("Error reading serial... Trying to flush it.")
                self._serial.flushInput()
                self._serial.flushOutput()
                self._debug("[read ] flush - '{}'".format(repr(ack).replace("'", "")))
                return ack.replace("\r", "")
        self._debug("[read ] '{}'".format(repr(ack).replace("'", "")))
        return ack.replace("\r", "")

    @lock
    def switch_on(self):
        ret = "ACK" in self._command("MEADE FLAT_WEAK LIGAR")
        if ret:
            self._light_on = True
        return ret

    @lock
    def switch_off(self):
        ret = "ACK" in self._command("MEADE FLAT_WEAK DESLIGAR")
        if ret:
            self._light_on = False
        return ret

    def is_switched_on(self):
        return self._light_on

    def is_slit_open(self):
        # FIXME: bool(self._command("MEADE PROG STATUS")[19])
        return self._slitOpen

    @lock
    def open_slit(self):
        self.log.debug("Opening dome slit.")
        ack = "ACK" in self._command("MEADE TRAPEIRA ABRIR")
        if ack:
            self._slitOpen = True
        return ack

    @lock
    def close_slit(self):
        self.log.debug("Closing dome slit.")
        ack = "ACK" in self._command("MEADE TRAPEIRA FECHAR")
        if ack:
            self._slitOpen = False
        return ack

    def _get_tag(self):
        ack = self._command("MEADE PROG STATUS")[8:11]
        if ack == "   ":
            self.log.info("Initializing dome...")
            self._init_dome()
            time.sleep(1)
            self.log.info("Dome initialized.")
            ack = float(self._command("MEADE PROG STATUS")[8:11])
        else:
            ack = float(ack)
        return ack

    @lock
    def get_az(self, tag=None):

        if tag is not None:
            ack = tag
        else:
            ack = self._get_tag()

        if ack < 846:  # 270 to 360 deg
            az = 270 + (ack - 801) * 2
        else:  # 0 to 270 deg
            az = (ack - 846) * 2

        return Coord.from_d(az)

    @lock
    def _init_dome(self):
        self._debug("Initializing dome...")
        self._resetDome(reset_tag=900)

    @lock
    def slew_to_az(self, az):
        # Dome is formed of tags with numbers from 801 to 982 (0 to 360 degress) where 801 is placed in the degree 270.
        if az > 360:
            raise InvalidDomePositionException(
                f"Cannot slew to {az}. Outside azimuth limits."
            )

        # Stop any running tasks on dome controller.
        # self._command('MEADE PROG PARAR')
        # time.sleep(.5)

        # Calculate the dome azimuth offset.
        tel = self.get_telescope()
        if not tel or not tel.is_tracking():
            if not tel.is_tracking():
                self.log.debug(
                    "Telescope is not Tracking. Ignoring the dome lookup table."
                )
            if not tel:
                self.log.error(
                    "I need to know the telescope position to use the lookup table!"
                )
            if az >= 270:
                dome_tag = int(math.ceil((az - 270) / 2.0 + 801))
            else:
                dome_tag = int(math.ceil(az / 2.0 + 846))
        else:
            alt, az = tel.get_position_alt_az()
            dome_tag = self._lookup.get_tag_altaz(alt, az)
            # Don't move if we are on the right position.
            if abs(dome_tag - self._get_tag()) <= self._dome_precision:
                return True

        # TODO: Abort point.
        # Run dome move command.
        # Works on the first try?
        if "ACK" in self._command("MEADE DOMO MOVER = %03d" % dome_tag):
            time.sleep(1)
        else:  # If not, reset the dome and try more few times...
            time.sleep(2)
            ack = self._command("MEADE DOMO MOVER = %03d" % dome_tag)
            for i_retry in range(self._restart_tries):
                if not ack.startswith("ACK"):
                    self.log.debug("No ACK from dome when trying to slew. Retrying...")
                    # Reset the dome.
                    reset_tag = dome_tag - 100
                    if reset_tag < 801:
                        reset_tag = 982 - (801 - reset_tag)
                    self._resetDome(reset_tag)
                    time.sleep(2)
                    ack = self._command("MEADE DOMO MOVER = %03d" % dome_tag)
                    time.sleep(1)

        self.slew_begin(az)
        t0 = time.time()
        while not self._checkIdle():
            if time.time() - t0 > self["slew_timeout"]:
                self.log.debug("Timeout moving the dome. Resetting...")
                # Reset the dome.
                reset_tag = dome_tag - 100
                if reset_tag < 801:
                    reset_tag = 982 - (801 - reset_tag)
                self._resetDome(reset_tag)
                break
            time.sleep(1)

        # Check the final position.
        # If the position is too far from the desired (restart_precision),
        # try to restart the dome and put it on the correct position.
        for i_retry in range(self._restart_tries):
            t0 = time.time()
            tag_now = self._get_tag()
            if abs(tag_now - dome_tag) < self._restart_precision:
                # If position is wrong for less than restart_precision, just confirm.
                self.slew_complete(self.get_az(tag_now), DomeStatus.OK)
                return True

            else:

                self.log.debug(
                    "Dome position error >= %i. Restart dome try %i."
                    % (self._restart_precision, i_retry)
                )

                # Reset the dome.
                reset_tag = dome_tag - 100
                if reset_tag < 801:
                    reset_tag = 982 - (801 - reset_tag)
                self._resetDome(reset_tag)

                ack = ""
                for i in range(self._restart_tries):
                    if not ack.startswith("ACK"):
                        ack = self._command("MEADE DOMO MOVER = %03d" % dome_tag)
                        time.sleep(2)

                # Try to move the dome again
                while not self._checkIdle():
                    if time.time() - t0 > self["slew_timeout"]:
                        self.log.debug("Timeout moving the dome")
                    time.sleep(1)

    def track(self):
        super(DomeBase, self).track()
        self.log.debug("Sleeping 15s after tracking enabled...")
        time.sleep(15)

    def abort_slew(self):
        return NotImplementedError()

    def is_slewing(self):
        return not self._checkIdle()


def get_metadata(self, request):
    # Check first if there is metadata from an metadata override method.
    md = self.get_metadata_override(request)
    if md is not None:
        return md
    # If not, just go on with the instrument's default metadata.
    if self.is_slit_open():
        slit = "Open"
    else:
        slit = "Closed"

    return [
        ("DOME_MDL", str(self["model"]), "Dome Model"),
        ("DOME_TYP", str(self["style"]), "Dome Type"),
        ("DOME_TRK", str(self["mode"]), "Dome Tracking/Standing"),
        ("DOME_AZ", str(self.get_az()), "Dome Azimuth"),
        ("DOME_SLT", str(slit), "Dome slit status"),
    ]
