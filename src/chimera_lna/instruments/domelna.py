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

from chimera_lna.util.dome_geometry import DomeGeometry


class DomeSlewTimeoutException(ChimeraException):
    """
    Raised when dome times out when slewing.
    """


class DomeCloseFailedException(ChimeraException):
    """
    Raised when the slit could not be closed. This is the one dome failure
    that must never be silent: an open slit in the rain damages the
    telescope, so the operator has to hear about it.
    """


class DomeLNA(DomeBase, LampBase):
    """
    COTE/LNA custom dome.

    The dome is composed of tags numbered from 801 to 982 (0 to 360 degrees,
    2 degrees per tag) where tag 801 is placed at azimuth 270 degrees.

    `device` accepts anything supported by pyserial's serial_for_url: a real
    serial port ("/dev/ttyS0") or a socket bridge ("socket://host:port"),
    which is how the chimera_lna.simulators.dome simulator is reached.

    Failure policy: the dome is a noisy, EMI-prone serial device that goes
    away (controller reset, USB re-enumeration, cable) and comes back on its
    own. Almost nothing here raises. A dedicated I/O thread owns the port and
    keeps trying to reconnect forever; status queries answer from the last
    known frame while the link is down, and motion commands retry and then
    defer to the control loop, which re-queues the move on the next cycle.
    An exception escaping the driver would abort whatever asked (an exposure
    gathering FITS headers, a whole scheduled program) for a fault that
    usually heals itself in seconds.
    """

    __config__ = {
        "model": "COTE/LNA custom dome",
        "style": Style.Classic,
        "az_resolution": 2,  # will not move if (delta az) < 2 deg
        "serial_timeout": 10.0,  # seconds
        "retry_delay": 2.0,  # seconds between command retries
        "poll_interval": 1.0,  # seconds between dome status polls
        "motion_wait": 20.0,  # seconds to wait for a running motion command
        "io_deadline": 30.0,  # seconds a command may spend retrying the port
        "heal_interval": 5.0,  # seconds between reconnect probes while down
        # GEM dome geometry, in units of the dome radius, fitted to the 2016
        # mapping run (see util/dome_geometry.py, scripts/fit_dome_geometry.py)
        "latitude": -22.5344,
        "mount_offset_east": -0.0384,
        "mount_offset_north": -0.0142,
        "mount_offset_up": -0.4356,
        "gem_offset": -0.3899,
        "dome_az_offset": 19.73,  # tag-ring zero point error, degrees
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
        self._reconnect_delays = (0.5, 2.0, 5.0)

        # Link health. While the dome is not answering, status queries skip
        # the port entirely (they answer from the cache) and the worker
        # probes for recovery every heal_interval, forever.
        self._io_healthy = True
        self._next_heal = 0.0

        # Motion commands exclude each other with a bounded wait: a caller
        # that cannot start within motion_wait gives up instead of parking a
        # bus worker for a whole slew. RLock: slew_to_az can reach
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

        # Built lazily: config is only applied after __init__
        self._geometry = None

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
        # and check the controller answers idle. A dome that is off or
        # unplugged must not stop the instrument from starting: the worker
        # keeps probing and the dome joins in when it answers.
        try:
            self._reset_dome(reset_tag=self._park_tag)
            self.get_az()
            self._check_idle()
        except Exception as e:
            self.log.warning(f"Dome did not answer during startup ({e}).")
        return super().__start__()

    def __stop__(self):
        super().__stop__()
        self._io_queue.put(None)
        if self._io_thread is not None:
            self._io_thread.join(timeout=self["serial_timeout"] + 5)

    # ------------------------------------------------------------------
    # serial I/O: everything below _io_loop runs on the worker thread only
    # ------------------------------------------------------------------

    def _create_serial(self):
        return serial.serial_for_url(
            self["device"], baudrate=9600, timeout=self["serial_timeout"]
        )

    def _close(self):
        if self._serial is not None and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass

    def _io_loop(self):
        """
        Sole owner of the serial port: opens it, runs every command
        transaction, and probes for recovery while the dome is down. Exits
        only on the None sentinel.
        """
        self._open_port()
        while True:
            try:
                item = self._io_queue.get(timeout=self["heal_interval"])
            except queue.Empty:
                self._heal()
                continue
            if item is None:
                break
            cmd, future, deadline = item
            try:
                if not self._io_healthy and time.monotonic() < self._next_heal:
                    # link known bad with a probe already scheduled: answer
                    # immediately so callers fall back to the cache instead
                    # of waiting out a serial timeout they cannot win
                    future.set_result("")
                else:
                    future.set_result(self._attempt(cmd, deadline))
            except Exception as e:
                # the worker must outlive any single command
                self.log.exception(f"Dome I/O worker error on '{cmd}' ({e}).")
                if not future.done():
                    future.set_result("")
        self._close()
        self._fail_pending()

    def _open_port(self):
        try:
            self._serial = self._create_serial()
            return True
        except Exception as e:
            self.log.warning(f"Could not open dome serial port ({e}).")
            self._serial = None
            self._mark_unhealthy()
            return False

    def _attempt(self, cmd, deadline):
        """
        Run cmd, reconnecting and retrying until the dome answers or the
        caller's deadline passes. Returns the reply, or "" when the dome
        never answered — callers read that as a missing ACK and retry.
        """
        while True:
            reply = ""
            try:
                if self._serial is None:
                    raise serial.SerialException("serial port is not open")
                reply = self._command_once(cmd)
            except (serial.SerialException, OSError, TypeError, ValueError) as e:
                # a half-open port (USB gone mid-read) fails inside pyserial
                # in more ways than SerialException alone
                self.log.warning(f"Serial error sending '{cmd}' ({e}).")
                self._debug(f"[error] '{cmd}' - {e}")
            if reply:
                self._mark_healthy()
                return reply
            self._mark_unhealthy()
            if time.monotonic() >= deadline:
                return ""
            self._reconnect(deadline)

    def _heal(self):
        """Probe a down link, forever, until the dome answers again."""
        if self._io_healthy or time.monotonic() < self._next_heal:
            return
        # schedule the next probe before running this one: the probe itself
        # can block for a whole serial timeout, and commands arriving in the
        # meantime must still fast-fail to the cache
        self._next_heal = time.monotonic() + self["heal_interval"]
        self._debug("[heal] probing the dome")
        if self._serial is None and not self._open_port():
            return
        reply = ""
        try:
            reply = self._command_once("MEADE PROG STATUS")
        except Exception as e:
            self._debug(f"[heal] {e}")
        if reply and self._parse_status(reply) is not None:
            self._mark_healthy()
            return
        self._reconnect()
        self._mark_unhealthy()

    def _mark_healthy(self):
        if not self._io_healthy:
            self.log.info("Dome serial link recovered.")
        self._io_healthy = True

    def _mark_unhealthy(self):
        if self._io_healthy:
            self.log.warning(
                "Dome is not answering on the serial port; "
                "status will be served from the last known frame while "
                "the driver keeps trying to reconnect."
            )
        self._io_healthy = False
        self._next_heal = time.monotonic() + self["heal_interval"]

    def _reconnect(self, deadline=None):
        """
        Reopen the serial port after a fatal serial error (e.g. a USB
        re-enumeration makes reads return EOF and pyserial raise
        SerialException). Does not reset/move the dome, only the port.
        Runs on the I/O worker thread only; never raises.
        """
        self._close()
        self._serial = None
        for delay in self._reconnect_delays:
            if deadline is not None and time.monotonic() + delay >= deadline:
                return False
            time.sleep(delay)
            try:
                self._serial = self._create_serial()
                self.log.info("Reopened the dome serial port.")
                return True
            except Exception as e:
                self.log.warning(f"Dome reconnect failed ({e}).")
                self._serial = None
        return False

    def _fail_pending(self):
        while True:
            try:
                item = self._io_queue.get_nowait()
            except queue.Empty:
                return
            if item is not None and not item[1].done():
                item[1].set_result("")

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

    # ------------------------------------------------------------------
    # command interface (any thread)
    # ------------------------------------------------------------------

    def _command(self, cmd, deadline=None):
        """
        Queue cmd to the I/O worker and wait for the reply. Never raises:
        an unanswered command comes back as "", which every caller already
        treats as a missing ACK.
        """
        budget = self["io_deadline"] if deadline is None else deadline
        future = Future()
        self._io_queue.put((cmd, future, time.monotonic() + budget))
        try:
            return future.result(timeout=budget + 2 * self["serial_timeout"])
        except TimeoutError:
            self.log.warning(f"Dome did not answer '{cmd}' in time.")
            return ""

    def _command_with_retries(self, cmd, tries=None):
        """Send cmd until the dome ACKs it. Returns True on ACK."""
        tries = self._restart_tries if tries is None else tries
        for attempt in range(tries):
            if "ACK" in self._command(cmd):
                return True
            if attempt + 1 < tries:
                time.sleep(self["retry_delay"])
        return False

    def _reset_dome(self, reset_tag=None):
        # Reset the queue and restart the controller.
        self._command_with_retries("MEADE PROG PARAR")
        self._command_with_retries("MEADE PROG RESET")
        # the controller needs a moment to come back before it takes a move
        time.sleep(self["retry_delay"])

        if reset_tag is None:
            return

        # When resetting the dome, move it to the reset_tag
        self._command_with_retries(f"MEADE DOMO MOVER = {reset_tag:03d}")
        self._wait_idle(time.monotonic() + self["slew_timeout"])

    # A well-formed STATUS frame: 8 spaces, 3-digit tag, space, '*' and 16
    # status bits. Motor EMI corrupts single bytes while the dome moves, so
    # any frame that does not match exactly is discarded instead of trusted.
    _status_re = re.compile(r"^ {8}(\d{3}) \*([01]{16})$")
    _status_blank_re = re.compile(r"^ {11} \*[01]{16}$")

    def _parse_status(self, ack):
        """
        Parse a MEADE PROG STATUS reply, refreshing the cache on success.

        Returns (tag, busy) for a well-formed frame, "blank" for a valid
        frame with an empty tag field (dome not initialized), or None for a
        corrupted/unanswered reply.
        """
        m = self._status_re.match(ack)
        if m and 801 <= int(m.group(1)) <= 982:
            tag, busy = int(m.group(1)), m.group(2)[3] == "1"
            self._status_cache = (tag, busy, time.monotonic())
            return tag, busy
        if self._status_blank_re.match(ack):
            return "blank"
        if ack:
            self.log.debug(f"Discarding invalid dome status frame ({ack!r}).")
        return None

    def _get_status(self):
        # one attempt: a status read must never sit through a reconnect
        # cycle, callers fall back to the cached frame instead
        deadline = self["serial_timeout"] + self["retry_delay"]
        return self._parse_status(self._command("MEADE PROG STATUS", deadline))

    def _check_idle(self):
        status = self._get_status()
        if not isinstance(status, tuple):
            # NAK, blank or corrupted frame: report busy, callers keep polling
            return False
        return not status[1]

    def _wait_idle(self, deadline):
        """Poll until the controller is idle. Returns False on timeout."""
        while not self._check_idle():
            if time.monotonic() >= deadline:
                self.log.debug("Timed out waiting for the dome to become idle.")
                return False
            time.sleep(self["poll_interval"])
        return True

    def _debug(self, msg):
        if self._debug_log:
            print(
                time.time(),
                threading.current_thread().name,
                msg,
                file=self._debug_log,
            )
            self._debug_log.flush()

    def switch_on(self):
        ret = self._command_with_retries("MEADE FLAT_WEAK LIGAR")
        if ret:
            self._light_on = True
        return ret

    def switch_off(self):
        ret = self._command_with_retries("MEADE FLAT_WEAK DESLIGAR")
        if ret:
            self._light_on = False
        return ret

    def is_switched_on(self):
        return self._light_on

    def is_slit_open(self):
        # FIXME: bool(self._command("MEADE PROG STATUS")[19])
        return self._slit_open

    def _acquire_motion(self):
        """
        Serialize whole motion sequences (slew, slit, init). The I/O queue
        already serializes port access; this only keeps two motion sequences
        from interleaving. Returns False instead of raising when the dome is
        busy: the caller logs and the control loop retries.
        """
        return self._motion_lock.acquire(timeout=self["motion_wait"])

    def open_slit(self):
        if not self._acquire_motion():
            self.log.warning("Dome busy: not opening the slit now.")
            return False
        try:
            self.log.debug("Opening dome slit.")
            ack = self._command_with_retries("MEADE TRAPEIRA ABRIR")
            if ack:
                self._slit_open = True
                self.slit_opened(self.get_az())
            else:
                self.log.error("Dome did not acknowledge the slit open command.")
            return ack
        finally:
            self._motion_lock.release()

    def close_slit(self):
        # Closing is the one command that must not fail quietly: keep the
        # port busy with retries and raise if the dome never acknowledges,
        # so the supervisor flags an error and tells the operator.
        acquired = self._acquire_motion()
        if not acquired:
            self.log.warning("Dome busy while closing the slit; closing anyway.")
        try:
            self.log.debug("Closing dome slit.")
            ack = self._command_with_retries(
                "MEADE TRAPEIRA FECHAR", tries=2 * self._restart_tries
            )
            if not ack:
                raise DomeCloseFailedException(
                    "Dome did not acknowledge the slit close command."
                )
            self._slit_open = False
            self.slit_closed(self.get_az())
            return ack
        finally:
            if acquired:
                self._motion_lock.release()

    def _get_tag(self):
        """Current dome tag, or None when the dome will not say."""
        for _ in range(self._status_tries):
            status = self._get_status()
            if isinstance(status, tuple):
                return float(status[0])
            if status == "blank":
                self.log.info("Initializing dome...")
                self._init_dome()
                time.sleep(self["poll_interval"])
                continue
            if not self._io_healthy:
                # the link is down and healing in the background: retrying
                # here only delays the caller
                break
            time.sleep(self["retry_delay"])
        self.log.debug("Could not read a valid dome position.")
        return None

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
        """
        Best-effort (tag, busy). Fresh cache first, then the dome, then the
        last frame it ever sent — a status query never raises and never
        waits on a link that is known to be down.
        """
        cached = self._cached_status()
        if cached is not None:
            return cached
        if self._io_healthy:
            tag = self._get_tag()
            if tag is not None:
                return tag, self._status_cache[1]
        if self._status_cache is not None:
            return self._status_cache[0], self._status_cache[1]
        return None, False

    def get_az(self, tag=None):
        if tag is None:
            tag, _ = self._read_status()
        if tag is None:
            # the dome has never answered since startup: any number here is
            # a guess, so use the configured init position and say so
            self.log.warning(
                f"Dome position unknown; assuming the init azimuth {self._init_az}."
            )
            return float(self._init_az)
        return float(self._tag_to_az(tag))

    def is_slewing(self):
        _, busy = self._read_status()
        return busy

    def _init_dome(self):
        if not self._acquire_motion():
            self.log.warning("Dome busy: skipping initialization.")
            return
        try:
            self._debug("Initializing dome...")
            self._reset_dome(reset_tag=self._park_tag)
            self.log.info("Dome initialized.")
        finally:
            self._motion_lock.release()

    def _get_tracking_telescope(self):
        """
        Returns a proxy of the telescope if it is available and tracking,
        None otherwise.
        """
        try:
            telescope = self.telescope
            if not telescope.ping():
                self.log.error(
                    "I need to know the telescope position to correct for the "
                    "dome geometry!"
                )
                return None
            if not telescope.is_tracking():
                self.log.debug("Telescope is not Tracking. Ignoring the dome geometry.")
                return None
            return telescope
        except Exception as e:
            self.log.debug(f"Telescope not available ({e}). Slaving the dome on-axis.")
            return None

    def _dome_geometry(self):
        if self._geometry is None:
            self._geometry = DomeGeometry(
                latitude=self["latitude"],
                mount_offset=(
                    self["mount_offset_east"],
                    self["mount_offset_north"],
                    self["mount_offset_up"],
                ),
                gem_offset=self["gem_offset"],
                az_offset=self["dome_az_offset"],
            )
        return self._geometry

    def _target_tag(self, az):
        """
        Dome tag for a telescope pointing: from the offset-mount geometry
        model when the telescope is tracking (the LNA telescope is off the
        dome axis), on-axis otherwise.
        """
        telescope = self._get_tracking_telescope()
        if telescope is None:
            return self._az_to_tag(az)
        try:
            alt, telescope_az = telescope.get_position_alt_az()
            return self._az_to_tag(self._dome_geometry().dome_az(alt, telescope_az))
        except Exception as e:
            self.log.warning(f"Could not use the dome geometry model ({e}).")
            return self._az_to_tag(az)

    def _on_target(self, dome_tag, precision):
        tag_now = self._get_tag()
        if tag_now is None:
            return False
        return self._tag_distance(tag_now, dome_tag) <= precision

    def slew_to_az(self, az):
        """
        Move the dome. Returns True when the dome reached the target.

        Never raises on a dome fault: in Track mode the control loop
        re-queues the move on the next cycle, so a dome that is briefly
        unreachable keeps being retried instead of aborting the exposure
        (or the whole program) that asked for the sync.
        """
        if az > 360:
            raise InvalidDomePositionException(
                f"Cannot slew to {az}. Outside azimuth limits."
            )
        if not self._acquire_motion():
            self.log.warning(f"Dome busy: cannot slew to {az} right now.")
            return False
        try:
            return self._do_slew_to_az(az)
        except Exception as e:
            self.log.exception(f"Dome slew to {az} failed ({e}).")
            return False
        finally:
            self._motion_lock.release()

    def _do_slew_to_az(self, az):
        if not self._io_healthy:
            # the worker is already reconnecting; spinning here would only
            # burn the slew timeout against a port that cannot answer
            self.log.warning(f"Dome is not answering: postponing the slew to {az}.")
            return False

        dome_tag = self._target_tag(az)

        # Don't move (nor disturb the controller) if already on position.
        if self._on_target(dome_tag, self._dome_precision):
            return True

        deadline = time.monotonic() + self["slew_timeout"]
        self.slew_begin(az)

        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            # MOVER is NAKed while the controller is busy: if a previous
            # command left the dome moving, wait for it instead of
            # triggering a reset.
            self._wait_idle(deadline)

            if not self._command_with_retries(f"MEADE DOMO MOVER = {dome_tag:03d}"):
                self.log.debug("No ACK from dome when trying to slew. Restarting...")
                self._reset_dome(self._recovery_tag(dome_tag))
                continue

            self._wait_idle(deadline)

            # If the position is off by more than restart_precision, restart
            # the dome and drive it to the target again.
            if self._on_target(dome_tag, self._restart_precision):
                self.slew_complete(self.get_az(), DomeStatus.OK)
                return True

            self.log.debug(
                f"Dome position error >= {self._restart_precision} tags "
                f"(attempt {attempt}). Restarting dome."
            )
            self._reset_dome(self._recovery_tag(dome_tag))

        self.log.warning(
            f"Dome did not reach tag {dome_tag} within {self['slew_timeout']}s. "
            "Will retry on the next control cycle."
        )
        self.slew_complete(self.get_az(), DomeStatus.ABORTED)
        return False

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
        """Stop the dome where it is (PARAR)."""
        return self._command_with_retries("MEADE PROG PARAR")

    def is_sync_with_tel(self):
        # The LNA telescope is off the dome axis: dome az != telescope az by
        # design (median 24 deg), so the base on-axis |tel_az - dome_az|
        # check reports "not synced" for almost every correct pointing. Ask
        # the question slew_to_az answers instead: are we on the tag the
        # geometry model wants for the telescope's alt/az?
        try:
            telescope = self._get_tracking_telescope()
            if telescope is None:
                return super().is_sync_with_tel()
            alt, telescope_az = telescope.get_position_alt_az()
            dome_tag = self._az_to_tag(self._dome_geometry().dome_az(alt, telescope_az))
            tag, _ = self._read_status()
            if tag is None:
                return False
            return self._tag_distance(tag, dome_tag) <= self._dome_precision
        except Exception as e:
            # answering "not synced" only picks a log line in the caller;
            # raising would abort the exposure asking the question
            self.log.warning(f"Could not check the dome sync ({e}).")
            return False

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
            # the live mode: self["mode"] is only the one the dome started in
            ("DOME_TRK", str(self.get_mode()), "Dome Tracking/Standing"),
            ("DOME_AZ", str(self.get_az()), "Dome Azimuth"),
            ("DOME_SLT", str(slit), "Dome slit status"),
        ]
