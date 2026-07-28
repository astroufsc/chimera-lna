#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2014-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Standalone LNA dome controller liveness probe.

Talks to the COTE/LNA dome controller exactly like the initial version of
the DomeLNA driver did - open the port at 9600 8N1, write "<cmd>\\r", read
until "\\r" or timeout - with zero chimera dependencies. Prints every byte
received (repr + hex) so a garbled link (wrong baud, EMI) is
distinguishable from a dead one (controller off, wrong port, wiring).

Safe to run while chimera is up: the port is opened non-exclusively, and
the commands sent (STATUS/PARAR/RESET) never move the dome. If chimera is
polling the port at the same time, replies may be split between the two
readers - stop chimera for a clean read.

Usage:
    dome_serial_probe.py [device] [--baud 9600] [--timeout 3]
"""

import argparse
import sys
import time

import serial

# opd-40 dome port (see chimera.production.config)
DEFAULT_DEVICE = (
    "/dev/serial/by-id/"
    "usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0"
)

COMMANDS = [
    "MEADE PROG STATUS",
    "MEADE PROG STATUS",
    "MEADE PROG PARAR",
    "MEADE PROG RESET",
    "MEADE PROG STATUS",
]


def read_reply(tty, timeout):
    t0 = time.time()
    data = b""
    while b"\r" not in data and time.time() - t0 < timeout:
        waiting = tty.in_waiting
        data += tty.read(waiting if waiting else 1)
    return data


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("device", nargs="?", default=DEFAULT_DEVICE)
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--listen",
        type=float,
        default=2.0,
        help="seconds to listen for unsolicited bytes before sending",
    )
    args = parser.parse_args()

    print(f"opening {args.device} @ {args.baud} 8N1 (timeout {args.timeout}s)")
    tty = serial.serial_for_url(args.device, baudrate=args.baud, timeout=args.timeout)
    print("open ok")

    received = b""

    print(f"listening {args.listen}s for unsolicited bytes...")
    t0 = time.time()
    while time.time() - t0 < args.listen:
        waiting = tty.in_waiting
        if waiting:
            chunk = tty.read(waiting)
            received += chunk
            print(f"  unsolicited: {chunk!r}")
        time.sleep(0.1)

    for cmd in COMMANDS:
        tty.reset_input_buffer()
        tty.reset_output_buffer()
        print(f"> {cmd!r}")
        tty.write(f"{cmd}\r".encode())
        reply = read_reply(tty, args.timeout)
        received += reply
        if reply:
            print(f"<   {reply!r}  hex: {reply.hex(' ')}")
        else:
            print("<   (nothing)")
        time.sleep(0.5)

    tty.close()
    if received:
        print(f"\nRESULT: got {len(received)} byte(s) - link ALIVE (even if garbled).")
        return 0
    print("\nRESULT: total silence - controller dead/off, or wrong port/wiring.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
