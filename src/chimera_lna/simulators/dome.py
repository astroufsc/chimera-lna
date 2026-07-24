# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""
TCP simulator of the COTE/LNA dome controller.

The real dome controller is reached over a serial line and speaks the "MEADE"
protocol. This simulator serves the same protocol over TCP so that DomeLNA can
talk to it through pyserial's socket:// URL handler exactly as it would talk
to the real hardware:

    dome:
      - name: dome
        type: DomeLNA
        device: socket://127.0.0.1:5001

Run it standalone with:

    python -m chimera_lna.simulators.dome --port 5001
"""

import argparse
import math
import socket
import socketserver
import threading
import time

MIN_TAG = 801
MAX_TAG = 982


class _DomeRequestHandler(socketserver.BaseRequestHandler):
    def handle(self):
        simulator = self.server.simulator
        simulator._connections.add(self.request)
        buffer = b""
        try:
            while True:
                try:
                    data = self.request.recv(1024)
                except (ConnectionError, OSError):
                    break
                if not data:
                    break
                buffer += data
                while b"\r" in buffer:
                    line, _, buffer = buffer.partition(b"\r")
                    response = simulator.process_command(line.decode().strip())
                    self.request.sendall(f"{response}\r".encode())
        finally:
            simulator._connections.discard(self.request)


class _DomeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class DomeSimulator:
    """
    End-to-end simulator of the LNA dome controller.

    The dome moves at `tags_per_second` and reports itself busy while moving,
    so clients see the same behavior as with the real hardware: an ACK to the
    move command followed by busy status polls until the position is reached.

    Supported commands:
        MEADE PROG STATUS         -> "        nnn *bbbbbbbbbbbbbbbb" (tag at
                                     [8:11], 16 status bits, busy at [16])
        MEADE PROG PARAR          -> stop movement
        MEADE PROG RESET          -> restart controller
        MEADE DOMO MOVER = NNN    -> move to tag NNN (801..982)
        MEADE TRAPEIRA ABRIR      -> open slit
        MEADE TRAPEIRA FECHAR     -> close slit
        MEADE FLAT_WEAK LIGAR     -> switch flat lamp on
        MEADE FLAT_WEAK DESLIGAR  -> switch flat lamp off
    Any other command is answered with NAK.
    """

    def __init__(self, host="127.0.0.1", port=0, initial_tag=900, tags_per_second=5.0):
        self._host = host
        self._port = port

        self._lock = threading.Lock()
        self._position = float(initial_tag)
        self._target = float(initial_tag)
        self._move_started = None
        self.tags_per_second = tags_per_second

        self.slit_open = False
        self.lamp_on = False

        self._server = None
        self._thread = None
        self._connections = set()

    # lifecycle

    def start(self):
        self._server = _DomeServer((self._host, self._port), _DomeRequestHandler)
        self._server.simulator = self
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="DomeSimulator", daemon=True
        )
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join()
            self._server = None
            self._thread = None

    def drop_connections(self):
        """Sever every live client connection (simulates a serial/USB drop);
        the server keeps listening, so clients can reconnect."""
        for conn in list(self._connections):
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def device(self):
        """pyserial URL to reach this simulator (socket://host:port)."""
        return f"socket://{self._host}:{self.port}"

    # dome physics

    def _update_position(self):
        """Advance the dome position according to the elapsed move time."""
        if self._move_started is None:
            return
        traveled = (time.monotonic() - self._move_started) * self.tags_per_second
        distance = abs(self._target - self._position)
        if traveled >= distance:
            self._position = self._target
            self._move_started = None
        else:
            self._position += math.copysign(traveled, self._target - self._position)
            self._move_started = time.monotonic()

    @property
    def current_tag(self):
        with self._lock:
            self._update_position()
            return int(round(self._position))

    @property
    def is_moving(self):
        with self._lock:
            self._update_position()
            return self._move_started is not None

    # protocol

    def process_command(self, command):
        if command == "MEADE PROG STATUS":
            with self._lock:
                self._update_position()
                busy = self._move_started is not None
                tag = int(round(self._position))
            # Real controller frame: 8 spaces, 3-digit tag, ' *' and 16 status
            # bits. DomeLNA validates this layout strictly (busy bit at [16]).
            bits = f"00{int(not busy)}{int(busy)}" + "0" * 12
            return f"        {tag:03d} *{bits}"

        elif command == "MEADE PROG PARAR":
            with self._lock:
                self._update_position()
                self._target = self._position
                self._move_started = None
            return "ACK"

        elif command == "MEADE PROG RESET":
            return "ACK"

        elif command.startswith("MEADE DOMO MOVER = "):
            try:
                target = int(command.rsplit("=", 1)[1])
            except ValueError:
                return "NAK"
            if not MIN_TAG <= target <= MAX_TAG:
                return "NAK"
            with self._lock:
                self._update_position()
                self._target = float(target)
                if self._target != self._position:
                    self._move_started = time.monotonic()
            return "ACK"

        elif command == "MEADE TRAPEIRA ABRIR":
            self.slit_open = True
            return "ACK"

        elif command == "MEADE TRAPEIRA FECHAR":
            self.slit_open = False
            return "ACK"

        elif command == "MEADE FLAT_WEAK LIGAR":
            self.lamp_on = True
            return "ACK"

        elif command == "MEADE FLAT_WEAK DESLIGAR":
            self.lamp_on = False
            return "ACK"

        return "NAK"


def main(args=None):
    parser = argparse.ArgumentParser(description="LNA dome controller simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--initial-tag", type=int, default=900)
    parser.add_argument(
        "--tags-per-second",
        type=float,
        default=5.0,
        help="dome speed (the real dome does ~5 tags/s)",
    )
    options = parser.parse_args(args)

    simulator = DomeSimulator(
        host=options.host,
        port=options.port,
        initial_tag=options.initial_tag,
        tags_per_second=options.tags_per_second,
    )
    simulator.start()
    print(f"LNA dome simulator listening on {simulator.device}")
    print(f'Use device: "{simulator.device}" in the DomeLNA configuration.')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        simulator.stop()


if __name__ == "__main__":
    main()
