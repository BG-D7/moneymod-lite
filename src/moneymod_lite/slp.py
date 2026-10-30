"""A minimal Minecraft Java Edition Server List Ping client.

mcstatus can't connect through a proxy, so the status ping is done here over a
socket that may be routed through SOCKS or an HTTP CONNECT proxy. See
https://minecraft.wiki/w/Java_Edition_protocol/Server_List_Ping
"""

from __future__ import annotations

import json
import os
import socket
import struct
import time
from typing import Any

import socks

from moneymod_lite.proxy import Proxy, ProxyFailed

# Any reasonably recent protocol version works for a status request.
PROTOCOL_VERSION = 47
MAX_PACKET = 1 << 21


def encode_varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Decode a varint from `data` at `offset`, returning it and the new offset."""
    result = 0
    for shift in range(0, 35, 7):
        if offset >= len(data):
            raise ValueError("truncated varint")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, offset
    raise ValueError("varint too long")


def _string(value: str) -> bytes:
    data = value.encode("utf-8")
    return encode_varint(len(data)) + data


def _packet(packet_id: int, payload: bytes = b"") -> bytes:
    data = encode_varint(packet_id) + payload
    return encode_varint(len(data)) + data


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("server closed the connection")
        buf += chunk
    return bytes(buf)


def _recv_packet(sock: socket.socket) -> tuple[int, bytes]:
    # The length prefix is at most 3 bytes for packets we accept.
    header = b""
    while True:
        header += _recv_exact(sock, 1)
        if not header[-1] & 0x80:
            break
        if len(header) >= 3:
            raise ValueError("packet too large")
    length, _ = decode_varint(header)
    if not 0 < length <= MAX_PACKET:
        raise ValueError(f"bad packet length {length}")
    data = _recv_exact(sock, length)
    packet_id, offset = decode_varint(data)
    return packet_id, data[offset:]


def connect(host: str, port: int, timeout: float, proxy: Proxy | None) -> socket.socket:
    """Open a TCP connection, through `proxy` if given.

    Raises `ProxyFailed` if the proxy itself is unusable, and `OSError` if the
    target can't be reached (directly or as reported by the proxy).
    """
    if proxy is None:
        return socket.create_connection((host, port), timeout=timeout)
    try:
        return socks.create_connection(
            (host, port),
            timeout=timeout,
            proxy_type=proxy.proxy_type,
            proxy_addr=proxy.host,
            proxy_port=proxy.port,
            proxy_rdns=proxy.remote_dns,
            proxy_username=proxy.username,
            proxy_password=proxy.password,
        )
    except socks.ProxyError as e:
        if _target_unreachable(e):
            raise ConnectionError(f"via {proxy}: {e}") from e
        raise ProxyFailed(f"{proxy}: {e}") from e


def _target_unreachable(error: socks.ProxyError) -> bool:
    """Whether the proxy worked but reported that it couldn't reach the target.

    PySocks often wraps the proxy's reply in a GeneralProxyError, with the
    real error in `socket_err`.
    """
    for e in (error, getattr(error, "socket_err", None)):
        if isinstance(e, socks.SOCKS5AuthError):
            return False
        if isinstance(e, (socks.SOCKS5Error, socks.SOCKS4Error)):
            # e.g. "connection refused", "host unreachable"
            return True
        if isinstance(e, socks.HTTPError):
            # 407 means our credentials were rejected; other statuses (502
            # etc.) mean the proxy couldn't reach the target.
            return "407" not in str(e)
    # Can't reach the proxy, timeouts, malformed replies: blame the proxy, so
    # that the caller tries another one.
    return False


def status(
    host: str, port: int, timeout: float = 5.0, proxy: Proxy | None = None
) -> tuple[dict[str, Any], float]:
    """Request the server status. Returns the status JSON and latency in ms."""
    with connect(host, port, timeout, proxy) as sock:
        sock.settimeout(timeout)
        handshake = (
            encode_varint(PROTOCOL_VERSION)
            + _string(host)
            + struct.pack(">H", port)
            + encode_varint(1)  # next state: status
        )
        start = time.perf_counter()
        sock.sendall(_packet(0x00, handshake) + _packet(0x00))

        packet_id, payload = _recv_packet(sock)
        latency_ms = (time.perf_counter() - start) * 1000
        if packet_id != 0x00:
            raise ValueError(f"unexpected packet 0x{packet_id:02x} in status reply")
        length, offset = decode_varint(payload)
        data = json.loads(payload[offset : offset + length].decode("utf-8"))

        # Prefer the ping/pong round trip for latency, as the client does, but
        # fall back to the status round trip if the server doesn't answer.
        token = struct.unpack(">q", os.urandom(8))[0]
        try:
            start = time.perf_counter()
            sock.sendall(_packet(0x01, struct.pack(">q", token)))
            packet_id, payload = _recv_packet(sock)
            if packet_id == 0x01 and payload[:8] == struct.pack(">q", token):
                latency_ms = (time.perf_counter() - start) * 1000
        except (OSError, ValueError):
            pass
    return data, latency_ms
