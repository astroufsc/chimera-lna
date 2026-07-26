# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""
End-to-end tests: a real DomeLNA instrument, started through the chimera
Manager lifecycle, talking over a TCP socket to the dome controller simulator
as if it were the real hardware.
"""

import re
import socket
import threading
import time

import pytest
from chimera.instruments.faketelescope import FakeTelescope

from chimera_lna.instruments.domelna import DomeLNA
from chimera_lna.simulators.dome import DomeSimulator

# fast dome: full turn in less than a second
SIMULATOR_SPEED = 500.0  # tags/s
FAST_TIMINGS = {"retry_delay": 0.01, "poll_interval": 0.01}
# same, plus short serial/heal budgets so the failure paths (which are all
# timeout-driven) run in seconds instead of minutes
DEAD_LINK_TIMINGS = {
    **FAST_TIMINGS,
    "serial_timeout": 0.5,
    "io_deadline": 2.0,
    "heal_interval": 0.2,
    "slew_timeout": 3.0,
    "motion_wait": 2.0,
}


@pytest.fixture
def simulator():
    with DomeSimulator(initial_tag=850, tags_per_second=SIMULATOR_SPEED) as simulator:
        yield simulator


@pytest.fixture
def dome(simulator, manager):
    yield manager.add_class(
        DomeLNA, "lna", config={"device": simulator.device, **FAST_TIMINGS}
    )


def raw_command(simulator, command):
    """Talk to the simulator directly over a plain TCP socket."""
    with socket.create_connection(("127.0.0.1", simulator.port), timeout=5) as sock:
        sock.sendall(f"{command}\r".encode())
        response = b""
        while not response.endswith(b"\r"):
            response += sock.recv(1024)
    return response.decode().replace("\r", "")


class TestDomeSimulatorProtocol:
    """Protocol-level tests against the bare TCP server."""

    def test_status_layout(self):
        with DomeSimulator(initial_tag=900) as simulator:
            response = raw_command(simulator, "MEADE PROG STATUS")
            assert re.fullmatch(r" {8}\d{3} \*[01]{16}", response)
            assert response[8:11] == "900"
            assert response[16] == "0"  # idle

    def test_move_reports_busy_then_arrives(self):
        with DomeSimulator(initial_tag=900, tags_per_second=20) as simulator:
            assert raw_command(simulator, "MEADE DOMO MOVER = 910") == "ACK"
            response = raw_command(simulator, "MEADE PROG STATUS")
            assert response[16] == "1"  # busy while moving
            time.sleep(10 / 20 + 0.1)  # 10 tags at 20 tags/s
            response = raw_command(simulator, "MEADE PROG STATUS")
            assert response[16] == "0"
            assert response[8:11] == "910"

    def test_parar_stops_movement(self):
        with DomeSimulator(initial_tag=900, tags_per_second=10) as simulator:
            raw_command(simulator, "MEADE DOMO MOVER = 950")
            time.sleep(0.2)
            assert raw_command(simulator, "MEADE PROG PARAR") == "ACK"
            assert not simulator.is_moving
            assert 900 <= simulator.current_tag < 950

    def test_unknown_command_naks(self):
        with DomeSimulator() as simulator:
            assert raw_command(simulator, "MEADE FOO BAR") == "NAK"

    def test_move_out_of_range_naks(self):
        with DomeSimulator() as simulator:
            assert raw_command(simulator, "MEADE DOMO MOVER = 999") == "NAK"
            assert raw_command(simulator, "MEADE DOMO MOVER = 800") == "NAK"


class TestDomeLNAUnits:
    def test_tag_az_round_trip(self):
        # tag 801 is at azimuth 270, tag 846 is at azimuth 0
        assert DomeLNA._tag_to_az(801) == 270
        assert DomeLNA._tag_to_az(846) == 0
        assert DomeLNA._az_to_tag(270) == 801
        assert DomeLNA._az_to_tag(0) == 846
        # note: the tag ring overlaps itself past a full turn (981 -> az 270,
        # same as 801; 982 -> az 272, same as 802), so stop at 980
        for tag in (801, 820, 846, 900, 950, 980):
            assert DomeLNA._az_to_tag(DomeLNA._tag_to_az(tag)) == tag

    def test_tag_distance_wraps(self):
        assert DomeLNA._tag_distance(905, 905) == 0
        assert DomeLNA._tag_distance(905, 907) == 2
        # 981 overlaps 801: physically adjacent across the wrap
        assert DomeLNA._tag_distance(802, 981) == 1
        assert DomeLNA._tag_distance(801, 980) == 1
        assert DomeLNA._tag_distance(805, 895) == 90

    def test_status_frame_validation(self):
        # clean frames, as captured from the real controller
        m = DomeLNA._status_re.match("        900 *0010000000000000")
        assert m and m.group(1) == "900" and m.group(2)[3] == "0"  # idle
        m = DomeLNA._status_re.match("        805 *0001010000101000")
        assert m and m.group(2)[3] == "1"  # busy
        # EMI-corrupted frames captured on the wire must all be rejected
        for frame in (
            "    �   805 *0011010000101000",
            "        979 *0015010010001000",
            "        979 *0p11010010001000",
            '    "   979 *0011010010001000',
            "       $885 j0001010000101000",
            "          0 *0001010000101000",
            "        960 +0001010000101000",
            "        960 ������j�",
            "NAK",
            "",
        ):
            assert DomeLNA._status_re.match(frame) is None, frame
            assert DomeLNA._status_blank_re.match(frame) is None, frame
        # blank tag (dome not initialized) is a distinct, valid case
        assert DomeLNA._status_blank_re.match("            *0001010000101000")


class TestDomeLNALifecycle:
    """Full lifecycle through the chimera Manager and the TCP simulator."""

    def test_start_resets_dome_to_park_tag(self, dome, simulator):
        # __start__ resets the dome and slews it to the park tag (900):
        # the simulator, which started at tag 850, must have seen the move.
        assert simulator.current_tag == 900
        assert dome.get_az() == pytest.approx(DomeLNA._tag_to_az(900))

    def test_slew_to_az(self, dome, simulator):
        fired = []
        dome.slew_begin += lambda az: fired.append("slew_begin")
        dome.slew_complete += lambda az, status: fired.append("slew_complete")

        dome.slew_to_az(180.0)

        assert dome.get_az() == pytest.approx(180.0, abs=2 * dome["az_resolution"])
        assert simulator.current_tag == DomeLNA._az_to_tag(180.0)
        assert not dome.is_slewing()

        # events go through the real bus: give them a moment to be delivered
        t0 = time.time()
        while len(fired) < 2 and time.time() - t0 < 5:
            time.sleep(0.05)
        assert fired == ["slew_begin", "slew_complete"]

    def test_slew_to_invalid_az_raises(self, dome):
        # exceptions raised behind the bus reach the proxy as a generic
        # Exception carrying the original exception name in the message
        with pytest.raises(Exception, match="InvalidDomePositionException"):
            dome.slew_to_az(361.0)

    def test_slit(self, dome, simulator):
        assert not dome.is_slit_open()
        assert not simulator.slit_open
        dome.open_slit()
        assert dome.is_slit_open()
        assert simulator.slit_open
        dome.close_slit()
        assert not dome.is_slit_open()
        assert not simulator.slit_open

    def test_flat_lamp(self, dome, simulator):
        assert not dome.is_switched_on()
        assert not simulator.lamp_on
        dome.switch_on()
        assert dome.is_switched_on()
        assert simulator.lamp_on
        dome.switch_off()
        assert not dome.is_switched_on()
        assert not simulator.lamp_on

    def test_metadata(self, dome):
        metadata = dict(
            (keyword, value) for keyword, value, _ in dome.get_metadata(None)
        )
        assert metadata["DOME_MDL"] == "COTE/LNA custom dome"
        assert metadata["DOME_SLT"] == "Closed"
        assert "DOME_AZ" in metadata

    def test_is_sync_with_tel_uses_lookup_table(self, simulator, manager):
        # off-axis dome: dome az != telescope az by design, so the base
        # on-axis check would report "not synced" for a correctly positioned
        # dome. The override must agree with where slew_to_az goes.
        telescope = manager.add_class(FakeTelescope, "fake")
        telescope.start_tracking()
        dome = manager.add_class(
            DomeLNA,
            "sync",
            config={
                "device": simulator.device,
                "telescope": "/FakeTelescope/fake",
                **FAST_TIMINGS,
            },
        )
        # with the telescope tracking, the az argument is overridden by the
        # lookup-table tag for the telescope's alt/az
        dome.slew_to_az(0.0)
        assert dome.is_sync_with_tel()

    def test_shutdown_closes_connection(self, simulator, manager):
        manager.add_class(
            DomeLNA, "stop", config={"device": simulator.device, **FAST_TIMINGS}
        )
        manager.remove("/DomeLNA/stop")
        # after __stop__ the driver must have disconnected: the simulator
        # still answers new connections
        assert raw_command(simulator, "MEADE PROG STATUS")[8:11].isdigit()


class _FastReconnectDome(DomeLNA):
    """DomeLNA with sub-second reconnect backoff, for failure-path tests."""

    def __init__(self):
        super().__init__()
        self._reconnect_delays = (0.05, 0.1)


def _proxy(manager, path):
    """A fresh per-thread proxy (get_proxy needs a full bus URL)."""
    return manager.get_proxy(
        f"tcp://{manager.get_hostname()}:{manager.get_port()}{path}"
    )


class TestDomeLNAConcurrency:
    """The serial port is owned by one I/O thread: status queries must keep
    answering during a slew, motion sequences must exclude each other, and a
    broken link must degrade gracefully - never wedge a caller and never
    raise into the exposure path."""

    def test_status_reads_answer_during_slew(self, manager):
        with DomeSimulator(initial_tag=900, tags_per_second=30) as simulator:
            manager.add_class(
                DomeLNA, "lna", config={"device": simulator.device, **FAST_TIMINGS}
            )
            errors, latencies = [], []

            def slew():
                try:
                    _proxy(manager, "/DomeLNA/lna").slew_to_az(180.0)
                except Exception as e:
                    errors.append(e)

            def hammer():
                proxy = _proxy(manager, "/DomeLNA/lna")
                t_end = time.time() + 1.0
                while time.time() < t_end:
                    t0 = time.time()
                    try:
                        proxy.get_az()
                        proxy.is_slewing()
                        proxy.is_slit_open()
                    except Exception as e:
                        errors.append(e)
                        return
                    latencies.append(time.time() - t0)

            slewer = threading.Thread(target=slew)
            slewer.start()
            t0 = time.time()
            while not simulator.is_moving and time.time() - t0 < 5:
                time.sleep(0.01)
            assert simulator.is_moving

            hammerers = [threading.Thread(target=hammer) for _ in range(3)]
            for thread in hammerers:
                thread.start()
            for thread in hammerers:
                thread.join()
            slewer.join()

            assert errors == []
            assert latencies and max(latencies) < 2.0
            assert simulator.current_tag == DomeLNA._az_to_tag(180.0)

    def test_concurrent_motion_declines_without_raising(self, manager):
        with DomeSimulator(initial_tag=900, tags_per_second=20) as simulator:
            dome = manager.add_class(
                DomeLNA,
                "lna",
                config={
                    "device": simulator.device,
                    "motion_wait": 0.3,
                    **FAST_TIMINGS,
                },
            )
            slewer = threading.Thread(
                target=lambda: _proxy(manager, "/DomeLNA/lna").slew_to_az(180.0)
            )
            slewer.start()
            t0 = time.time()
            while not simulator.is_moving and time.time() - t0 < 5:
                time.sleep(0.01)
            assert simulator.is_moving

            # busy is a False, not an exception: the caller (a checklist
            # item, an exposure sync) must not be killed by a busy dome
            assert dome.open_slit() is False
            assert dome.is_slit_open() is False
            slewer.join()

    def test_reconnects_after_connection_drop(self, dome, simulator):
        simulator.drop_connections()
        # next command hits the dead socket, reconnects (the server is
        # still listening) and retries transparently
        dome.slew_to_az(180.0)
        assert simulator.current_tag == DomeLNA._az_to_tag(180.0)

    def test_dead_port_degrades_instead_of_raising(self, manager):
        # the 2026-07-26 failure: a dome hiccup raised
        # "Dome serial I/O timed out" out of the FITS-header gathering and
        # killed the exposure. Nothing a camera calls may raise.
        simulator = DomeSimulator(initial_tag=900, tags_per_second=500.0).start()
        dome = manager.add_class(
            _FastReconnectDome,
            "lna",
            config={"device": simulator.device, **DEAD_LINK_TIMINGS},
        )
        last_az = dome.get_az()
        simulator.drop_connections()
        simulator.stop()

        t0 = time.time()
        assert dome.get_az() == last_az  # last known frame, not an exception
        assert dome.is_slewing() is False
        assert dome.is_slit_open() is False
        assert isinstance(dome.is_sync_with_tel(), bool)
        assert dome.slew_to_az(180.0) is False
        assert dome.get_metadata(None)  # header gathering still works
        assert time.time() - t0 < 30

    def test_recovers_on_its_own_when_the_dome_comes_back(self, manager):
        # a power-cycled controller (2026-07-25) must not need a chimera
        # restart: the worker keeps probing until the dome answers again
        simulator = DomeSimulator(initial_tag=900, tags_per_second=500.0).start()
        port = simulator.port
        dome = manager.add_class(
            _FastReconnectDome,
            "lna",
            config={"device": simulator.device, **DEAD_LINK_TIMINGS},
        )
        assert dome.slew_to_az(150.0) is True
        simulator.drop_connections()
        simulator.stop()
        assert dome.slew_to_az(180.0) is False  # down, and says so

        recovered = DomeSimulator(port=port, initial_tag=900, tags_per_second=500.0)
        recovered.start()
        try:
            t0 = time.time()
            while time.time() - t0 < 20:
                if dome.slew_to_az(180.0):
                    break
                time.sleep(0.2)
            assert recovered.current_tag == DomeLNA._az_to_tag(180.0)
        finally:
            recovered.stop()

    def test_close_slit_raises_when_the_dome_never_answers(self, simulator, manager):
        # the one exception left: an open slit that silently fails to close
        # is a hazard, so the supervisor must hear about it
        dome = manager.add_class(
            _FastReconnectDome,
            "lna",
            config={"device": simulator.device, **DEAD_LINK_TIMINGS},
        )
        dome.open_slit()
        assert dome.is_slit_open()
        simulator.muted = True  # controller hung: link up, no replies
        with pytest.raises(Exception, match="DomeCloseFailed|close"):
            dome.close_slit()
        assert dome.is_slit_open()  # state must not lie about a failed close

    def test_muted_controller_does_not_raise_on_status(self, simulator, manager):
        # port healthy, controller silent (2026-07-23): reads must fall back
        # to the last known frame instead of raising a serial timeout
        dome = manager.add_class(
            _FastReconnectDome,
            "lna",
            config={"device": simulator.device, **DEAD_LINK_TIMINGS},
        )
        last_az = dome.get_az()
        simulator.muted = True
        t0 = time.time()
        assert dome.get_az() == last_az
        assert dome.is_slewing() is False
        assert time.time() - t0 < 20

        simulator.muted = False
        t0 = time.time()
        while time.time() - t0 < 20:
            if dome.slew_to_az(180.0):
                break
            time.sleep(0.2)
        assert simulator.current_tag == DomeLNA._az_to_tag(180.0)
