from unittest import mock

import pytest
import requests

from moneymod_lite import checker
from moneymod_lite.checker import Queue, Status
from moneymod_lite.proxy import Proxy, ProxyPool
from tests.fakes import minecraft_server, socks5_proxy


def test_eta() -> None:
    queue = Queue(regular=100, prio=2, eta_factor=2.0, eta_power=1.5)
    assert queue.eta_seconds() == pytest.approx(2.0 * 100**1.5)
    assert queue.eta_seconds(4) == pytest.approx(16.0)
    assert queue.eta_seconds(0) == 0.0
    assert Queue(regular=100, prio=2).eta_seconds() is None


class FakeSession:
    """Stands in for requests.Session, answering by URL."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url: str, proxies: dict | None, timeout: float):
        self.calls.append((url, proxies))
        body = self.responses[url]
        if isinstance(body, Exception):
            raise body
        response = mock.Mock()
        response.json.return_value = body
        return response


QUEUE = {"time": "x", "prio": 2, "regular": 742}
ETA = {"factor": 199.0, "pow": 1.07}


def test_fetch_queue() -> None:
    session = FakeSession(
        {
            "https://api.2b2t.vc/queue": QUEUE,
            "https://api.2b2t.vc/queue/eta-equation": ETA,
        }
    )
    queue = checker.fetch_queue(session=session)
    assert queue == Queue(regular=742, prio=2, eta_factor=199.0, eta_power=1.07)


def test_fetch_queue_without_eta() -> None:
    session = FakeSession(
        {
            "https://api.2b2t.vc/queue": QUEUE,
            "https://api.2b2t.vc/queue/eta-equation": requests.HTTPError("500"),
        }
    )
    assert checker.fetch_queue(session=session) == Queue(regular=742, prio=2)


def test_fetch_queue_skips_broken_proxy() -> None:
    bad, good = Proxy("socks5", "bad", 1), Proxy("socks5", "good", 1)

    class Session(FakeSession):
        def get(self, url, proxies, timeout):
            if proxies and "bad" in proxies["https"]:
                self.calls.append((url, proxies))
                raise requests.ConnectionError("proxy down")
            return super().get(url, proxies, timeout)

    session = Session(
        {
            "https://api.2b2t.vc/queue": QUEUE,
            "https://api.2b2t.vc/queue/eta-equation": ETA,
        }
    )
    pool = ProxyPool([bad, good])
    assert checker.fetch_queue(pool, session=session).regular == 742
    assert [p["https"] for _, p in session.calls] == [
        bad.url,
        good.url,
        good.url,
    ]
    assert pool.healthy_count() == 1


def test_ping_direct_and_through_proxy() -> None:
    with (
        minecraft_server(online=420) as (address, _),
        socks5_proxy() as ((proxy_host, proxy_port), targets),
    ):
        direct = checker.ping(address)
        pool = ProxyPool([Proxy("socks5h", proxy_host, proxy_port)])
        proxied = checker.ping(address, pool)
    assert direct.online == proxied.online == 420
    assert isinstance(direct, Status)
    assert targets == [address]


def test_resolve_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(host: str):
        raise RuntimeError("no dns")

    monkeypatch.setattr(checker.JavaServer, "lookup", boom)
    assert checker.resolve("example.org") == ("example.org", 25565)
    assert checker.resolve("example.org:25566") == ("example.org", 25566)
