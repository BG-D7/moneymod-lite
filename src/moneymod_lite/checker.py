"""Fetching the state of the server: a status ping and the 2b2t queue."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
from mcstatus import JavaServer

from moneymod_lite import __version__, slp
from moneymod_lite.proxy import Proxy, ProxyFailed, ProxyPool

QUEUE_API = "https://api.2b2t.vc"
USER_AGENT = f"moneymod-lite/{__version__} (+https://github.com/BG-D7/moneymod-lite)"


@dataclass(frozen=True)
class Status:
    online: int
    max_players: int
    latency_ms: float


@dataclass(frozen=True)
class Queue:
    regular: int
    prio: int
    # ETA seconds = factor * position ** power (see /queue/eta-equation).
    eta_factor: float | None = None
    eta_power: float | None = None

    def eta_seconds(self, position: int | None = None) -> float | None:
        """Estimated wait for `position` in the regular queue (default: the end)."""
        if self.eta_factor is None or self.eta_power is None:
            return None
        if position is None:
            position = self.regular
        if position <= 0:
            return 0.0
        return self.eta_factor * position**self.eta_power


def resolve(host: str) -> tuple[str, int]:
    """Resolve a server address like the game does, following SRV records.

    Falls back to the address as given if the lookup fails.
    """
    try:
        address = JavaServer.lookup(host).address
    except Exception:  # dnspython raises many kinds of errors
        name, _, port = host.rpartition(":")
        if name and port.isdigit():
            return name, int(port)
        return host, JavaServer.DEFAULT_PORT
    return address.host, address.port


def ping(
    address: tuple[str, int], pool: ProxyPool | None = None, timeout: float = 5.0
) -> Status:
    """Ping a Java Edition server.

    Raises `OSError`/`ValueError` if the server is down, and
    `AllProxiesFailed` if no proxy could be used.
    """
    host, port = address

    def request(proxy: Proxy | None) -> Status:
        data, latency_ms = slp.status(host, port, timeout=timeout, proxy=proxy)
        players = data.get("players") or {}
        return Status(
            online=int(players.get("online", 0)),
            max_players=int(players.get("max", 0)),
            latency_ms=latency_ms,
        )

    return pool.run(request) if pool else request(None)


def _get_json(
    session: requests.Session, path: str, proxy: Proxy | None, timeout: float
) -> Any:
    proxies = {"http": proxy.url, "https": proxy.url} if proxy else None
    try:
        response = session.get(QUEUE_API + path, proxies=proxies, timeout=timeout)
    except (requests.ConnectionError, requests.Timeout) as e:
        # Through a SOCKS proxy, requests can't tell a dead proxy from an
        # unreachable target. The queue isn't used to detect restarts, so it's
        # safe to blame the proxy and try another.
        if proxy is None:
            raise
        raise ProxyFailed(f"{proxy}: {e}") from e
    response.raise_for_status()
    return response.json()


def fetch_queue(
    pool: ProxyPool | None = None,
    timeout: float = 5.0,
    session: requests.Session | None = None,
) -> Queue:
    """Fetch the current 2b2t queue length from api.2b2t.vc."""
    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT

    def request(proxy: Proxy | None) -> Queue:
        queue = _get_json(session, "/queue", proxy, timeout)
        try:
            eta = _get_json(session, "/queue/eta-equation", proxy, timeout)
            factor, power = float(eta["factor"]), float(eta["pow"])
        except ProxyFailed:
            raise
        except (requests.RequestException, ValueError, KeyError, TypeError):
            factor = power = None
        return Queue(
            regular=int(queue["regular"]),
            prio=int(queue["prio"]),
            eta_factor=factor,
            eta_power=power,
        )

    return pool.run(request) if pool else request(None)
