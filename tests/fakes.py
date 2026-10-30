"""Tiny local servers for tests: a Minecraft status server and a SOCKS5 proxy."""

from __future__ import annotations

import json
import socket
import struct
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from moneymod_lite.slp import decode_varint, encode_varint


def _recv_packet(conn: socket.socket) -> bytes:
    header = b""
    while not header or header[-1] & 0x80:
        header += conn.recv(1)
    length, _ = decode_varint(header)
    data = b""
    while len(data) < length:
        data += conn.recv(length - len(data))
    return data


def _send_packet(conn: socket.socket, packet_id: int, payload: bytes) -> None:
    data = encode_varint(packet_id) + payload
    conn.sendall(encode_varint(len(data)) + data)


def _serve(listener: socket.socket, handle) -> None:
    while True:
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


@contextmanager
def _listening(handle) -> Iterator[tuple[str, int]]:
    listener = socket.create_server(("127.0.0.1", 0))
    threading.Thread(target=_serve, args=(listener, handle), daemon=True).start()
    try:
        yield listener.getsockname()[:2]
    finally:
        listener.close()


@contextmanager
def minecraft_server(online: int = 420, max_players: int = 1000, pong: bool = True):
    """A server that answers one status request (and optionally a ping)."""
    seen_handshakes: list[bytes] = []

    def handle(conn: socket.socket) -> None:
        with conn:
            seen_handshakes.append(_recv_packet(conn))
            _recv_packet(conn)  # status request
            body = json.dumps(
                {
                    "version": {"name": "1.12.2", "protocol": 340},
                    "players": {"online": online, "max": max_players},
                    "description": {"text": "test"},
                }
            ).encode()
            _send_packet(conn, 0x00, encode_varint(len(body)) + body)
            if pong:
                ping = _recv_packet(conn)
                _send_packet(conn, 0x01, ping[1:])

    with _listening(handle) as address:
        yield address, seen_handshakes


@contextmanager
def socks5_proxy(username: str | None = None, password: str | None = None):
    """A SOCKS5 proxy (CONNECT only) that records where it was asked to go."""
    targets: list[tuple[str, int]] = []

    def pipe(src: socket.socket, dst: socket.socket) -> None:
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            pass
        finally:
            dst.close()

    def handle(conn: socket.socket) -> None:
        version, nmethods = conn.recv(2)
        methods = conn.recv(nmethods)
        if username is None:
            conn.sendall(b"\x05\x00")
        else:
            assert 0x02 in methods
            conn.sendall(b"\x05\x02")
            conn.recv(1)
            user = conn.recv(conn.recv(1)[0]).decode()
            pw = conn.recv(conn.recv(1)[0]).decode()
            if (user, pw) != (username, password):
                conn.sendall(b"\x01\x01")
                conn.close()
                return
            conn.sendall(b"\x01\x00")

        _, cmd, _, atyp = conn.recv(4)
        if atyp == 0x01:
            host = socket.inet_ntoa(conn.recv(4))
        elif atyp == 0x03:
            host = conn.recv(conn.recv(1)[0]).decode()
        else:
            conn.close()
            return
        (port,) = struct.unpack(">H", conn.recv(2))
        targets.append((host, port))
        try:
            upstream = socket.create_connection((host, port), timeout=5)
        except OSError:
            conn.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)  # connection refused
            conn.close()
            return
        conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        threading.Thread(target=pipe, args=(upstream, conn), daemon=True).start()
        pipe(conn, upstream)

    with _listening(handle) as address:
        yield address, targets


def unused_port() -> int:
    """A local port with nothing listening on it."""
    with socket.create_server(("127.0.0.1", 0)) as s:
        return s.getsockname()[1]
