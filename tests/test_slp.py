import pytest

from moneymod_lite import slp
from moneymod_lite.proxy import Proxy, ProxyFailed
from tests.fakes import minecraft_server, socks5_proxy, unused_port

# Generous, because Windows takes ~2s to report that a local port is closed
# (it retries the SYN), and the fake proxy must answer before we give up.
TIMEOUT = 10


@pytest.mark.parametrize("value", [0, 1, 127, 128, 300, 2**31 - 1, -1])
def test_varint_roundtrip(value: int) -> None:
    decoded, offset = slp.decode_varint(slp.encode_varint(value))
    assert decoded == value & 0xFFFFFFFF
    assert offset == len(slp.encode_varint(value))


def test_truncated_varint() -> None:
    with pytest.raises(ValueError):
        slp.decode_varint(b"\x80")


@pytest.mark.parametrize("pong", [True, False])
def test_status_direct(pong: bool) -> None:
    with minecraft_server(online=420, pong=pong) as ((host, port), handshakes):
        data, latency = slp.status(host, port, timeout=TIMEOUT)
    assert data["players"] == {"online": 420, "max": 1000}
    assert latency >= 0
    # Handshake: id 0, protocol 47, host, port, next state 1 (status).
    assert handshakes[0].startswith(b"\x00" + slp.encode_varint(47))
    assert handshakes[0].endswith(b"\x01")


def test_status_server_down() -> None:
    with pytest.raises(OSError):
        slp.status("127.0.0.1", unused_port(), timeout=TIMEOUT)


def test_status_through_socks5() -> None:
    with (
        minecraft_server(online=7) as ((mc_host, mc_port), _),
        socks5_proxy() as ((proxy_host, proxy_port), targets),
    ):
        proxy = Proxy("socks5h", proxy_host, proxy_port)
        data, _ = slp.status(mc_host, mc_port, timeout=TIMEOUT, proxy=proxy)
    assert data["players"]["online"] == 7
    assert targets == [(mc_host, mc_port)]


def test_status_through_socks5_with_auth() -> None:
    with (
        minecraft_server() as ((mc_host, mc_port), _),
        socks5_proxy("bob", "secret") as ((proxy_host, proxy_port), _),
    ):
        good = Proxy("socks5", proxy_host, proxy_port, "bob", "secret")
        assert slp.status(mc_host, mc_port, timeout=TIMEOUT, proxy=good)[0]
        bad = Proxy("socks5", proxy_host, proxy_port, "bob", "wrong")
        with pytest.raises(ProxyFailed):
            slp.status(mc_host, mc_port, timeout=TIMEOUT, proxy=bad)


def test_dead_proxy_is_a_proxy_failure() -> None:
    proxy = Proxy("socks5", "127.0.0.1", unused_port())
    with pytest.raises(ProxyFailed):
        slp.status("127.0.0.1", 25565, timeout=TIMEOUT, proxy=proxy)


def test_target_down_through_proxy_is_not_a_proxy_failure() -> None:
    with socks5_proxy() as ((proxy_host, proxy_port), _):
        proxy = Proxy("socks5", proxy_host, proxy_port)
        with pytest.raises(OSError) as excinfo:
            slp.status("127.0.0.1", unused_port(), timeout=TIMEOUT, proxy=proxy)
    assert not isinstance(excinfo.value, ProxyFailed)
