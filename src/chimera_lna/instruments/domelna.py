# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Driver for the COTE/LNA custom dome (serial "MEADE" protocol)."""

import math
import os
import threading
import time

import serial
from chimera.core import SYSTEM_CONFIG_DIRECTORY
from chimera.core.exceptions import ChimeraException
from chimera.core.lock import lock
from chimera.instruments.dome import DomeBase
from chimera.instruments.lamp import LampBase
from chimera.interfaces.dome import (
    DomeStatus,
    InvalidDomePositionException,
    Style,
)

from chimera_lna.util.lookup_table import DomeLookupTable


class DomeSlewTimeoutException(ChimeraException):
    """
    Raised when dome times out when slewing.
    """


class DomeLNA(DomeBase, LampBase):
    """
    COTE/LNA custom dome.

    The dome is composed of tags numbered from 801 to 982 (0 to 360 degrees,
    2 degrees per tag) where tag 801 is placed at azimuth 270 degrees.

    `device` accepts anything supported by pyserial's serial_for_url: a real
    serial port ("/dev/ttyS0") or a socket bridge ("socket://host:port"),
    which is how the chimera_lna.simulators.dome simulator is reached.
    """

    __config__ = {
        "model": "COTE/LNA custom dome",
        "style": Style.Classic,
        "az_resolution": 2,  # will not move if (delta az) < 2 deg
        "serial_timeout": 10.0,  # seconds
        "retry_delay": 2.0,  # seconds between command retries
        "poll_interval": 1.0,  # seconds between dome status polls
    }

    def __init__(self):
        DomeBase.__init__(self)
        LampBase.__init__(self)

        # Model, name, etc...
        self._light_on = False
        # self["park_position"] = 108
        self._park_tag = 900

        # Serial port
        self._serial = None

        # Few parameters...
        self._init_az = 108
        self._slit_open = False  # FIXME: Slit open/closed should come from the dome.

        # Error handling constants
        self._dome_precision = 2  # Number of tags = +/- 4 degrees
        self._restart_precision = 4  # Number of tags = +/- 8 degrees.
        self._restart_tries = 3

        # Load LookUp table
        self._lookup = DomeLookupTable()

        # Debug file
        self._debug_log = None
        try:
            self._debug_log = open(
                os.path.join(SYSTEM_CONFIG_DIRECTORY, "dome-debug.log"), "w"
            )
        except OSError as e:
            self.log.warning(f"Could not create dome debug file ({str(e)})")

    def __start__(self):
        self._open()
        return super().__start__()

    def __stop__(self):
        super().__stop__()
        self._close()

    def _create_serial(self):
        return serial.serial_for_url(
            self["device"], baudrate=9600, timeout=self["serial_timeout"]
        )

    def _open(self):
        # Open the serial Port.
        self._serial = self._create_serial()
        # On start, reset the dome.
        self._reset_dome(reset_tag=self._park_tag)
        # Get the dome azimuth just to move it to the init position if needed.
        self.get_az()
        # Check if connection is okay.
        self._check_idle()

    def _close(self):
        if self._serial is not None and self._serial.is_open:
            self._serial.close()

    def _reset_dome(self, reset_tag=None):
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        ack = ""
        # Reset the queue.
        for _ in range(self._restart_tries):
            if not ack.startswith("ACK"):
                ack = self._command("MEADE PROG PARAR")
                time.sleep(self["retry_delay"])
        # Restart controller
        ack = ""
        for _ in range(self._restart_tries):
            if not ack.startswith("ACK"):
                ack = self._command("MEADE PROG RESET")
                time.sleep(self["retry_delay"])

        if reset_tag is None:
            return

        # When resetting the dome, move it to the reset_tag
        ack = self._command(f"MEADE DOMO MOVER = {reset_tag:03d}")
        if not ack.startswith("ACK"):
            ack = self._command(f"MEADE DOMO MOVER = {reset_tag:03d}")
            time.sleep(self["retry_delay"])

        # Wait for the dome to finish moving
        t0 = time.time()
        while not self._check_idle():
            if time.time() - t0 > self["slew_timeout"]:
                self.log.debug("Timeout moving the dome")
                return
            time.sleep(self["poll_interval"])

    def _check_idle(self):
        ack = self._command("MEADE PROG STATUS")
        if ack.startswith("NAK"):
            self.log.debug("Got a NAK on status.")
            return False
        if len(ack) < 17:  # Sometimes ack is garbage. So, return that dome is busy.
            return False
        return ack[16] != "1"  # if '1', system busy

    def _debug(self, msg):
        if self._debug_log:
            print(
                time.time(),
                threading.current_thread().name,
                msg,
                file=self._debug_log,
            )
            self._debug_log.flush()

    def _command(self, cmd):
        self._serial.reset_output_buffer()
        self._serial.reset_input_buffer()
        self._debug(f"[write] '{cmd}'")
        self._serial.write(f"{cmd}\r".encode())
        t0 = time.time()
        ack = ""
        while "\r" not in ack:
            waiting = self._serial.in_waiting
            data = self._serial.read(waiting if waiting else 1)
            ack += data.decode(errors="replace")
            if (time.time() - t0) > self["serial_timeout"]:
                self.log.debug("Error reading serial... Trying to flush it.")
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
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
        return self._slit_open

    @lock
    def open_slit(self):
        self.log.debug("Opening dome slit.")
        ack = "ACK" in self._command("MEADE TRAPEIRA ABRIR")
        if ack:
            self._slit_open = True
            self.slit_opened(self.get_az())
        return ack

    @lock
    def close_slit(self):
        self.log.debug("Closing dome slit.")
        ack = "ACK" in self._command("MEADE TRAPEIRA FECHAR")
        if ack:
            self._slit_open = False
            self.slit_closed(self.get_az())
        return ack

    def _get_tag(self):
        ack = self._command("MEADE PROG STATUS")[8:11]
        if ack == "   ":
            self.log.info("Initializing dome...")
            self._init_dome()
            time.sleep(self["poll_interval"])
            self.log.info("Dome initialized.")
            ack = float(self._command("MEADE PROG STATUS")[8:11])
        else:
            ack = float(ack)
        return ack

    @staticmethod
    def _tag_to_az(tag):
        if tag < 846:  # 270 to 360 deg
            return 270 + (tag - 801) * 2
        else:  # 0 to 270 deg
            return (tag - 846) * 2

    @staticmethod
    def _az_to_tag(az):
        if az >= 270:
            return int(math.ceil((az - 270) / 2.0 + 801))
        else:
            return int(math.ceil(az / 2.0 + 846))

    @lock
    def get_az(self, tag=None):
        if tag is None:
            tag = self._get_tag()
        return float(self._tag_to_az(tag))

    @lock
    def _init_dome(self):
        self._debug("Initializing dome...")
        self._reset_dome(reset_tag=self._park_tag)

    def _get_tracking_telescope(self):
        """
        Returns a proxy of the telescope if it is available and tracking,
        None otherwise.
        """
        try:
            telescope = self.telescope
            if not telescope.ping():
                self.log.error(
                    "I need to know the telescope position to use the lookup table!"
                )
                return None
            if not telescope.is_tracking():
                self.log.debug(
                    "Telescope is not Tracking. Ignoring the dome lookup table."
                )
                return None
            return telescope
        except Exception as e:
            self.log.debug(f"Telescope not available ({e}). Using geometric model.")
            return None

    @lock
    def slew_to_az(self, az):
        if az > 360:
            raise InvalidDomePositionException(
                f"Cannot slew to {az}. Outside azimuth limits."
            )

        # Calculate the dome azimuth offset using the lookup table when the
        # telescope is available and tracking.
        telescope = self._get_tracking_telescope()
        if telescope is not None:
            alt, telescope_az = telescope.get_position_alt_az()
            dome_tag = self._lookup.get_tag_altaz(alt, telescope_az)
            # Don't move if we are on the right position.
            if abs(dome_tag - self._get_tag()) <= self._dome_precision:
                return True
        else:
            dome_tag = self._az_to_tag(az)

        # Run dome move command.
        # Works on the first try?
        if "ACK" in self._command(f"MEADE DOMO MOVER = {dome_tag:03d}"):
            time.sleep(self["poll_interval"])
        else:  # If not, reset the dome and try more few times...
            time.sleep(self["retry_delay"])
            ack = self._command(f"MEADE DOMO MOVER = {dome_tag:03d}")
            for _ in range(self._restart_tries):
                if not ack.startswith("ACK"):
                    self.log.debug("No ACK from dome when trying to slew. Retrying...")
                    self._reset_dome(self._recovery_tag(dome_tag))
                    time.sleep(self["retry_delay"])
                    ack = self._command(f"MEADE DOMO MOVER = {dome_tag:03d}")
                    time.sleep(self["poll_interval"])

        self.slew_begin(az)
        t0 = time.time()
        while not self._check_idle():
            if time.time() - t0 > self["slew_timeout"]:
                self.log.debug("Timeout moving the dome. Resetting...")
                self._reset_dome(self._recovery_tag(dome_tag))
                break
            time.sleep(self["poll_interval"])

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

            self.log.debug(
                f"Dome position error >= {self._restart_precision}. "
                f"Restart dome try {i_retry}."
            )

            self._reset_dome(self._recovery_tag(dome_tag))

            ack = ""
            for _ in range(self._restart_tries):
                if not ack.startswith("ACK"):
                    ack = self._command(f"MEADE DOMO MOVER = {dome_tag:03d}")
                    time.sleep(self["retry_delay"])

            # Try to move the dome again
            while not self._check_idle():
                if time.time() - t0 > self["slew_timeout"]:
                    self.log.debug("Timeout moving the dome")
                    break
                time.sleep(self["poll_interval"])

    @staticmethod
    def _recovery_tag(dome_tag):
        """
        Tag used to reset the dome when a slew to dome_tag fails.
        """
        reset_tag = dome_tag - 100
        if reset_tag < 801:
            reset_tag = 982 - (801 - reset_tag)
        return reset_tag

    def track(self):
        super().track()
        self.log.debug("Sleeping 15s after tracking enabled...")
        time.sleep(15)

    def abort_slew(self):
        raise NotImplementedError()

    def is_slewing(self):
        return not self._check_idle()

    def get_metadata(self, request):
        # Check first if there is metadata from an metadata override method.
        md = self.get_metadata_override(request)
        if md is not None:
            return md
        # If not, just go on with the instrument's default metadata.
        slit = "Open" if self.is_slit_open() else "Closed"

        return [
            ("DOME_MDL", str(self["model"]), "Dome Model"),
            ("DOME_TYP", str(self["style"]), "Dome Type"),
            ("DOME_TRK", str(self["mode"]), "Dome Tracking/Standing"),
            ("DOME_AZ", str(self.get_az()), "Dome Azimuth"),
            ("DOME_SLT", str(slit), "Dome slit status"),
        ]
