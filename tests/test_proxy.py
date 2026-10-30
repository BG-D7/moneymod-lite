from pathlib import Path

import pytest

from moneymod_lite.proxy import (
    AllProxiesFailed,
    Proxy,
    ProxyFailed,
    ProxyPool,
    load_proxies,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("socks5://1.2.3.4:1080", Proxy("socks5", "1.2.3.4", 1080)),
        ("socks5h://u:p%40ss@host:9050", Proxy("socks5h", "host", 9050, "u", "p@ss")),
        ("http://proxy.local", Proxy("http", "proxy.local", 8080)),
        ("socks4a://h:1", Proxy("socks4a", "h", 1)),
        ("1.2.3.4:1080", Proxy("socks5h", "1.2.3.4", 1080)),
        ("  SOCKS5://h:2  ", Proxy("socks5", "h", 2)),
    ],
)
def test_parse(text: str, expected: Proxy) -> None:
    assert Proxy.parse(text) == expected


@pytest.mark.parametrize("text", ["ftp://h:1", "socks5://", "socks5://h:notaport"])
def test_parse_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        Proxy.parse(text)


def test_str_hides_credentials() -> None:
    proxy = Proxy.parse("socks5://user:secret@h:1")
    assert "secret" not in str(proxy)
    assert proxy.url == "socks5://user:secret@h:1"


def test_load_proxies(tmp_path: Path) -> None:
    path = tmp_path / "proxies.txt"
    path.write_text(
        "# my proxies\nsocks5://a:1\n\nhttp://b:2  # office\nsocks5://a:1\n"
    )
    assert load_proxies(["socks5://c:3"], [path]) == [
        Proxy("socks5", "c", 3),
        Proxy("socks5", "a", 1),
        Proxy("http", "b", 2),
    ]


def test_load_proxies_reports_line(tmp_path: Path) -> None:
    path = tmp_path / "proxies.txt"
    path.write_text("socks5://a:1\nbogus://x\n")
    with pytest.raises(ValueError, match=r"proxies.txt:2"):
        load_proxies(files=[path])


A, B, C = (Proxy("socks5", name, 1) for name in "abc")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_round_robin() -> None:
    pool = ProxyPool([A, B, C])
    used = [pool.run(lambda p: p) for _ in range(4)]
    assert used == [A, B, C, A]


def test_failed_proxy_rests_then_returns() -> None:
    clock = FakeClock()
    pool = ProxyPool([A, B], cooldown=60, clock=clock)

    def request(proxy: Proxy) -> Proxy:
        if proxy is A:
            raise ProxyFailed("down")
        return proxy

    assert pool.run(request) is B  # A failed, fell through to B
    assert pool.healthy_count() == 1
    assert pool.candidates() == [B, A]  # A is resting, so tried last
    clock.now += 61
    assert pool.healthy_count() == 2


def test_all_failed() -> None:
    pool = ProxyPool([A, B])

    def request(proxy: Proxy) -> None:
        raise ProxyFailed(str(proxy))

    with pytest.raises(AllProxiesFailed):
        pool.run(request)


def test_target_errors_are_not_retried() -> None:
    pool = ProxyPool([A, B])
    tried = []

    def request(proxy: Proxy) -> None:
        tried.append(proxy)
        raise ConnectionRefusedError

    with pytest.raises(ConnectionRefusedError):
        pool.run(request)
    assert tried == [A]
    assert pool.healthy_count() == 2


def test_empty_pool() -> None:
    with pytest.raises(ValueError):
        ProxyPool([])
