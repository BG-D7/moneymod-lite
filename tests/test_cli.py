from datetime import datetime
from unittest import mock

import pytest

from moneymod_lite import cli
from moneymod_lite.checker import Queue, Status
from moneymod_lite.detector import Phase
from moneymod_lite.proxy import AllProxiesFailed

ADDRESS = ("connect.2b2t.org", 25565)


@pytest.fixture(autouse=True)
def no_dns():
    with mock.patch.object(cli, "resolve", return_value=ADDRESS):
        yield


NOW = datetime(2026, 10, 30, 12, 0, 0)


def test_format_line_up() -> None:
    line = cli.format_line(
        NOW,
        Status(online=420, max_players=1000, latency_ms=87.4),
        Queue(regular=10, prio=1, eta_factor=60.0, eta_power=1.0),
        Phase.UP,
    )
    assert line == (
        "12:00:00 | онлайн 420/1000, пинг 87 мс | "
        "очередь 10 (+1 prio), ~10 мин в конце очереди"
    )


def test_format_line_hides_fake_max() -> None:
    status = Status(online=1274, max_players=1, latency_ms=95.0)
    assert cli.format_line(NOW, status, None, Phase.UP) == (
        "12:00:00 | онлайн 1274, пинг 95 мс"
    )


def test_format_line_down() -> None:
    assert cli.format_line(NOW, None, None, Phase.DOWN) == (
        "12:00:00 | сервер не отвечает"
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(59, "1 мин"), (600, "10 мин"), (3600, "1 ч 0 мин"), (5460, "1 ч 31 мин")],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert cli.format_duration(seconds) == expected


def test_interval_too_small() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--interval", "1"])


def test_once(capsys: pytest.CaptureFixture[str]) -> None:
    status = Status(online=420, max_players=1000, latency_ms=87.0)
    with (
        mock.patch.object(cli, "ping", return_value=status),
        mock.patch.object(cli, "fetch_queue", side_effect=OSError),
    ):
        assert cli.main(["--once", "--no-color"]) == 0
    assert "онлайн 420/1000" in capsys.readouterr().out


def test_reports_restart(capsys: pytest.CaptureFixture[str]) -> None:
    status = Status(online=5, max_players=1000, latency_ms=50.0)
    pings = [OSError(), OSError(), status, KeyboardInterrupt()]

    def fake_ping(address, pool=None) -> Status:
        result = pings.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    with (
        mock.patch.object(cli, "ping", fake_ping),
        mock.patch.object(cli.time, "sleep"),
    ):
        assert cli.main(["--no-queue", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "Сервер ушёл на рестарт." in out
    assert "Рестарт прошёл!" in out


def test_proxy_failures_are_not_restarts(capsys: pytest.CaptureFixture[str]) -> None:
    status = Status(online=5, max_players=1000, latency_ms=50.0)
    pings = [
        status,
        AllProxiesFailed(),
        AllProxiesFailed(),
        status,
        KeyboardInterrupt(),
    ]

    def fake_ping(address, pool=None) -> Status:
        result = pings.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    with (
        mock.patch.object(cli, "ping", fake_ping),
        mock.patch.object(cli.time, "sleep"),
    ):
        assert (
            cli.main(["--no-queue", "--no-color", "--proxy", "socks5://127.0.0.1:1"])
            == 0
        )
    out = capsys.readouterr().out
    assert "Через прокси: 1 шт." in out
    assert "все прокси недоступны" in out
    assert "рестарт" not in out.lower()


def test_bad_proxy_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--proxy", "ftp://nope"]) == 2
    assert "Ошибка в списке прокси" in capsys.readouterr().err
