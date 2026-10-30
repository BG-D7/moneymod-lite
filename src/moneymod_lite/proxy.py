"""Parsing proxy lists and rotating through them."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from urllib.parse import unquote, urlsplit

import socks

T = TypeVar("T")

_SCHEMES = {
    "http": (socks.HTTP, False),
    "socks4": (socks.SOCKS4, False),
    "socks4a": (socks.SOCKS4, True),
    "socks5": (socks.SOCKS5, False),
    "socks5h": (socks.SOCKS5, True),
}
_DEFAULT_PORTS = {
    "http": 8080,
    "socks4": 1080,
    "socks4a": 1080,
    "socks5": 1080,
    "socks5h": 1080,
}


class ProxyFailed(Exception):
    """The proxy itself could not be used (unreachable, bad auth, ...).

    This is distinct from the proxy reporting that the *target* is down.
    """


class AllProxiesFailed(Exception):
    """Every proxy in the list failed."""


@dataclass(frozen=True)
class Proxy:
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @classmethod
    def parse(cls, text: str) -> Proxy:
        """Parse `scheme://[user:pass@]host[:port]`; `host:port` means socks5h."""
        text = text.strip()
        if "://" not in text:
            text = "socks5h://" + text
        parts = urlsplit(text)
        scheme = parts.scheme.lower()
        if scheme not in _SCHEMES:
            raise ValueError(
                f"unsupported proxy scheme {scheme!r} in {text!r} "
                f"(expected one of: {', '.join(_SCHEMES)})"
            )
        if not parts.hostname:
            raise ValueError(f"missing proxy host in {text!r}")
        try:
            port = parts.port or _DEFAULT_PORTS[scheme]
        except ValueError as e:
            raise ValueError(f"invalid proxy port in {text!r}") from e
        return cls(
            scheme=scheme,
            host=parts.hostname,
            port=port,
            username=unquote(parts.username) if parts.username else None,
            password=unquote(parts.password) if parts.password else None,
        )

    @property
    def proxy_type(self) -> int:
        return _SCHEMES[self.scheme][0]

    @property
    def remote_dns(self) -> bool:
        return _SCHEMES[self.scheme][1]

    @property
    def url(self) -> str:
        """The proxy as a URL for `requests`, including credentials."""
        auth = ""
        if self.username:
            auth = self.username + (f":{self.password}" if self.password else "") + "@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    def __str__(self) -> str:
        # Never print credentials.
        return f"{self.scheme}://{self.host}:{self.port}"


def load_proxies(urls: Iterable[str] = (), files: Iterable[Path] = ()) -> list[Proxy]:
    """Read proxies given directly and from files.

    Files have one proxy per line; `#` starts a comment.
    """
    proxies = [Proxy.parse(url) for url in urls]
    for path in files:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                proxies.append(Proxy.parse(line))
            except ValueError as e:
                raise ValueError(f"{path}:{number}: {e}") from e
    return list(dict.fromkeys(proxies))


class ProxyPool:
    """Round-robins requests over the proxies, resting ones that fail."""

    def __init__(
        self,
        proxies: Iterable[Proxy],
        cooldown: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.proxies = list(proxies)
        if not self.proxies:
            raise ValueError("no proxies given")
        self.cooldown = cooldown
        self._clock = clock
        self._next = 0
        self._failed_at: dict[Proxy, float] = {}

    def candidates(self) -> list[Proxy]:
        """Proxies to try for the next request, in order.

        Healthy proxies come first, starting one further along each time so
        that requests are spread over the list; resting proxies come last, so
        that they are still tried if nothing else works.
        """
        start = self._next
        self._next = (self._next + 1) % len(self.proxies)
        ordered = self.proxies[start:] + self.proxies[:start]
        healthy = [p for p in ordered if self._is_healthy(p)]
        resting = [p for p in ordered if not self._is_healthy(p)]
        return healthy + resting

    def _is_healthy(self, proxy: Proxy) -> bool:
        failed_at = self._failed_at.get(proxy)
        return failed_at is None or self._clock() - failed_at >= self.cooldown

    def mark_failed(self, proxy: Proxy) -> None:
        self._failed_at[proxy] = self._clock()

    def mark_ok(self, proxy: Proxy) -> None:
        self._failed_at.pop(proxy, None)

    def healthy_count(self) -> int:
        return sum(1 for p in self.proxies if self._is_healthy(p))

    def run(self, request: Callable[[Proxy], T]) -> T:
        """Make `request` through the first proxy that works.

        Errors other than `ProxyFailed` (e.g. the target being down) are
        raised immediately: trying another proxy wouldn't help.
        """
        last: ProxyFailed | None = None
        for proxy in self.candidates():
            try:
                result = request(proxy)
            except ProxyFailed as e:
                self.mark_failed(proxy)
                last = e
                continue
            self.mark_ok(proxy)
            return result
        raise AllProxiesFailed(str(last)) from last
