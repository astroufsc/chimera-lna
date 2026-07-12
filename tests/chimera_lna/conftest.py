# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later

import socket
import threading

import pytest
from chimera.core.bus import Bus
from chimera.core.manager import Manager


def free_tcp_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def manager():
    bus = Bus(f"tcp://127.0.0.1:{free_tcp_port()}")
    bus_thread = threading.Thread(target=bus.run_forever, name="Bus", daemon=True)
    bus_thread.start()
    manager = Manager(bus=bus)
    yield manager
    manager.shutdown()
    bus.shutdown()
    bus_thread.join(timeout=10)
