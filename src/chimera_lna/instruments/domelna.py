# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Driver for the COTE/LNA custom dome (serial "MEADE" protocol)."""

import math
import os
import queue
import re
import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager

import serial
from chimera.core import SYSTEM_CONFIG_DIRECTORY
from chimera.core.exceptions import ChimeraException
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
        "motion_wait": 20.0,  # seconds to wait for a running motion command
    }

    def __init__(self):
        DomeBase.__init__(self)
        LampBase.__init__(self)

        # Model, name, etc...
        self._light_on = False
        # self["park_position"] = 108
        self._park_tag = 900

        # Serial port: owned exclusively by the I/O worker thread. Callers
        # submit commands through _io_queue and wait on a Future, so exactly
        # one thread ever touches the port (no reconnect-under-reader races).
        self._serial = None
        self._io_queue = queue.Queue()
        self._io_thread = None
        self._reconnect_delays = (1, 5, 10)

        # Motion commands exclude each other with a bounded wait: a caller
        # that cannot start within motion_wait gets "Dome busy" instead of
        # parking a bus worker for a whole slew. RLock: slew_to_az can reach
        # _init_dome on the same thread through _get_tag.
        self._motion_lock = threading.RLock()

        # Few parameters...
        self._init_az = 108
        self._slit_open = False  # FIXME: Slit open/closed should come from the dome.

        # Error handling constants
        self._dome_precision = 2  # Number of tags = +/- 4 degrees
        self._restart_precision = 4  # Number of tags = +/- 8 degrees.
        self._restart_tries = 3
        self._status_tries = 5  # STATUS polls before giving up on a valid frame

        # Last valid STATUS frame: (tag, busy, monotonic timestamp).
        # get_az()/is_slewing() answer from this instead of queueing their
        # own STATUS command: a slew in progress polls STATUS every
        # poll_interval, so during motion the cache is always fresher than
        # the TTL and callers get an instant answer.
        self._status_cache = None
        self._status_cache_ttl = 2.0  # seconds; > poll_interval

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
        self._io_thread = threading.Thread(
            target=self._io_loop, name="DomeLNA-serial", daemon=True
        )
        self._io_thread.start()
        # On start, reset the dome to the park tag, read the position back
        # and check the controller answers idle.
        self._reset_dome(reset_tag=self._park_tag)
        self.get_az()
        self._check_idle()
        return super().__start__()

    def __stop__(self):
        super().__stop__()
        self._io_queue.put(None)
        if self._io_thread is not None:
            self._io_thread.join(timeout=self["serial_timeout"] + 5)

    def _create_serial(self):
        return serial.serial_for_url(
            self["device"], baudrate=9600, timeout=self["serial_timeout"]
        )

    def _close(self):
        if self._serial is not None and self._serial.is_open:
            self._serial.close()

    def _io_loop(self):
        """
        Sole owner of the serial port: opens it, runs every command
        transaction and reconnects on failure. Exits on the None sentinel,
        closing the port and failing whatever raced in after it.
        """
        try:
            self._serial = self._create_serial()
        except Exception as e:
            # keep serving: the first command will go through _reconnect()
            self.log.warning(f"Could not open dome serial port ({e}).")
        while True:
            item = self._io_queue.get()
            if item is None:
                break
            cmd, future = item
            try:
                future.set_result(self._transact(cmd))
            except Exception as e:
                future.set_exception(e)
        self._close()
        self._fail_pending(ChimeraException("Dome I/O worker stopped."))

    def _fail_pending(self, error):
        while True:
            try:
                item = self._io_queue.get_nowait()
            except queue.Empty:
                return
            if item is not None:
                item[1].set_exception(error)

    def _reset_dome(self, reset_tag=None):
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

    # A well-formed STATUS frame: 8 spaces, 3-digit tag, space, '*' and 16
    # status bits. Motor EMI corrupts single bytes while the dome moves, so
    # any frame that does not match exactly is discarded instead of trusted.
    _status_re = re.compile(r"^ {8}(\d{3}) \*([01]{16})$")
    _status_blank_re = re.compile(r"^ {11} \*[01]{16}$")

    def _get_status(self):
        """
        Send MEADE PROG STATUS and parse the reply.

        Returns (tag, busy) for a well-formed frame, "blank" for a valid
        frame with an empty tag field (dome not initialized), or None for a
        corrupted/unparseable reply.
        """
        ack = self._command("MEADE PROG STATUS")
        m = self._status_re.match(ack)
        if m and 801 <= int(m.group(1)) <= 982:
            tag, busy = int(m.group(1)), m.group(2)[3] == "1"
            self._status_cache = (tag, busy, time.monotonic())
            return tag, busy
        if self._status_blank_re.match(ack):
            return "blank"
        self.log.debug(f"Discarding invalid dome status frame ({ack!r}).")
        return None

    def _check_idle(self):
        status = self._get_status()
        if not isinstance(status, tuple):
            # NAK, blank or corrupted frame: report busy, callers keep polling
            return False
        return not status[1]

    def _debug(self, msg):
        if self._debug_log:
            print(
                time.time(),
                threading.current_thread().name,
                msg,
                file=self._debug_log,
            )
            self._debug_log.flush()

    def _reconnect(self):
        """
        Reopen the serial port after a fatal serial error (e.g. a USB
        re-enumeration makes reads return EOF and pyserial raise
        SerialException). Does not reset/move the dome, only the port.
        Runs on the I/O worker thread only.
        """
        self._close()
        for delay in self._reconnect_delays:
            try:
                self._serial = self._create_serial()
                self.log.info("Reconnected to the dome serial port.")
                return
            except serial.SerialException as e:
                self.log.warning(f"Dome reconnect failed ({e}). Retrying in {delay}s.")
                time.sleep(delay)
        self._serial = self._create_serial()

    def _command(self, cmd):
        """
        Queue cmd to the I/O worker and wait for the reply. The wait is
        bounded (one transaction plus a full reconnect cycle), so a sick
        port surfaces as an exception, never as an indefinitely parked
        bus worker.
        """
        future = Future()
        self._io_queue.put((cmd, future))
        deadline = 2 * self["serial_timeout"] + sum(self._reconnect_delays) + 5
        try:
            return future.result(timeout=deadline)
        except TimeoutError:
            raise ChimeraException(
                f"Dome serial I/O timed out after {deadline:.0f}s on '{cmd}'"
            ) from None

    def _transact(self, cmd):
        """One command transaction on the I/O worker thread."""
        try:
            if self._serial is None:
                raise serial.SerialException("serial port is not open")
            return self._command_once(cmd)
        except (serial.SerialException, OSError, TypeError, ValueError) as e:
            # a half-open port (USB gone mid-read) fails inside pyserial in
            # more ways than SerialException alone
            self.log.warning(f"Serial error sending '{cmd}' ({e}). Reconnecting...")
            self._debug(f"[error] '{cmd}' - {e}")
            self._reconnect()
            return self._command_once(cmd)

    def _command_once(self, cmd):
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

    @contextmanager
    def _motion(self):
        """
        Serialize motion commands (slew, slit, init) with a bounded wait:
        the I/O queue already serializes port access, this only keeps whole
        motion sequences from interleaving.
        """
        if not self._motion_lock.acquire(timeout=self["motion_wait"]):
            raise ChimeraException(
                "Dome busy: another motion command is running "
                f"(waited {self['motion_wait']:.0f}s)."
            )
        try:
            yield
        finally:
            self._motion_lock.release()

    def switch_on(self):
        ret = "ACK" in self._command("MEADE FLAT_WEAK LIGAR")
        if ret:
            self._light_on = True
        return ret

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

    def open_slit(self):
        with self._motion():
            self.log.debug("Opening dome slit.")
            ack = "ACK" in self._command("MEADE TRAPEIRA ABRIR")
            if ack:
                self._slit_open = True
                self.slit_opened(self.get_az())
            return ack

    def close_slit(self):
        with self._motion():
            self.log.debug("Closing dome slit.")
            ack = "ACK" in self._command("MEADE TRAPEIRA FECHAR")
            if ack:
                self._slit_open = False
                self.slit_closed(self.get_az())
            return ack

    def _get_tag(self):
        for _ in range(self._status_tries):
            status = self._get_status()
            if isinstance(status, tuple):
                return float(status[0])
            if status == "blank":
                self.log.info("Initializing dome...")
                self._init_dome()
                time.sleep(self["poll_interval"])
                self.log.info("Dome initialized.")
                continue
            time.sleep(self["retry_delay"])
        raise ChimeraException(
            f"Could not read a valid dome position after {self._status_tries} tries"
        )

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

    def _cached_status(self):
        """Return the last (tag, busy) if fresher than the TTL, else None."""
        cached = self._status_cache
        if cached and (time.monotonic() - cached[2]) <= self._status_cache_ttl:
            return cached[0], cached[1]
        return None

    def _read_status(self):
        """Fresh STATUS read through the I/O queue: _get_tag() refreshes
        the cache with the busy bit of the same frame."""
        tag = self._get_tag()
        return tag, self._status_cache[1]

    def get_az(self, tag=None):
        # cache first: a slew in progress refreshes the cache every
        # poll_interval, so callers get an instant answer during motion
        if tag is None:
            cached = self._cached_status()
            tag = cached[0] if cached else self._read_status()[0]
        return float(self._tag_to_az(tag))

    def _init_dome(self):
        with self._motion():
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

    def slew_to_az(self, az):
        with self._motion():
            return self._do_slew_to_az(az)

    def _do_slew_to_az(self, az):
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
        else:
            dome_tag = self._az_to_tag(az)

        # Don't move (nor disturb the controller) if already on position.
        if self._tag_distance(dome_tag, self._get_tag()) <= self._dome_precision:
            return True

        # MOVER is NAKed while the controller is busy: if a previous command
        # left the dome moving, wait for it instead of triggering a reset.
        t0 = time.time()
        while not self._check_idle():
            if time.time() - t0 > self["slew_timeout"]:
                self.log.warning("Dome busy for too long before slew. Resetting...")
                self._reset_dome()
                break
            time.sleep(self["poll_interval"])

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
            if self._tag_distance(tag_now, dome_tag) < self._restart_precision:
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

        self.slew_complete(self.get_az(), DomeStatus.ABORTED)
        raise DomeSlewTimeoutException(
            f"Dome did not reach tag {dome_tag} after "
            f"{self._restart_tries} restarts (currently at {self._get_tag():.0f})"
        )

    @staticmethod
    def _tag_distance(tag_a, tag_b):
        """
        Distance between two tags in tags, accounting for the wrap-around
        (the 182-tag range 801-982 covers a full turn: 981/982 overlap
        801/802, so one revolution is 180 tags).
        """
        distance = abs(tag_a - tag_b) % 180
        return min(distance, 180 - distance)

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
        # cache first, for the same reason as get_az()
        cached = self._cached_status()
        if cached is not None:
            return cached[1]
        return self._read_status()[1]

    def is_sync_with_tel(self):
        # The LNA telescope is off the dome axis: dome az != telescope az by
        # design (median 24 deg), so the base on-axis |tel_az - dome_az|
        # check reports "not synced" for almost every correct pointing. Ask
        # the question slew_to_az answers instead: are we on the tag the
        # lookup table wants for the telescope's alt/az?
        telescope = self._get_tracking_telescope()
        if telescope is None:
            return super().is_sync_with_tel()
        alt, telescope_az = telescope.get_position_alt_az()
        dome_tag = self._lookup.get_tag_altaz(alt, telescope_az)
        cached = self._cached_status()
        tag = cached[0] if cached else self._read_status()[0]
        return self._tag_distance(tag, dome_tag) <= self._dome_precision

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
