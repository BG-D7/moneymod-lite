"""Command-line entry point: poll the server and report restarts."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import colorama

from moneymod_lite import __version__
from moneymod_lite.checker import Queue, Status, fetch_queue, ping, resolve
from moneymod_lite.detector import Event, Phase, RestartDetector
from moneymod_lite.proxy import AllProxiesFailed, ProxyPool, load_proxies

MIN_INTERVAL = 5.0


def format_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} ч {minutes % 60} мин"


def format_line(
    now: datetime,
    status: Status | None,
    queue: Queue | None,
    phase: Phase,
    proxies_failed: bool = False,
) -> str:
    parts = [now.strftime("%H:%M:%S")]
    if proxies_failed:
        parts.append("все прокси недоступны")
    elif status is None:
        parts.append("сервер не отвечает" if phase is Phase.DOWN else "нет ответа")
    else:
        online = f"онлайн {status.online}"
        # Some servers (including 2b2t) report a fake maximum.
        if status.max_players >= status.online:
            online += f"/{status.max_players}"
        parts.append(f"{online}, пинг {status.latency_ms:.0f} мс")
    if queue is not None:
        text = f"очередь {queue.regular} (+{queue.prio} prio)"
        eta = queue.eta_seconds()
        if eta is not None:
            text += f", ~{format_duration(eta)} в конце очереди"
        parts.append(text)
    return " | ".join(parts)


def colour_for(status: Status | None, phase: Phase, proxies_failed: bool) -> str:
    if proxies_failed:
        return colorama.Fore.MAGENTA
    if status is not None:
        return colorama.Fore.GREEN
    if phase is Phase.DOWN:
        return colorama.Fore.RED
    return colorama.Fore.YELLOW


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="moneymod-lite",
        description="Следит за 2b2t и сообщает, когда прошёл рестарт.",
    )
    parser.add_argument(
        "--host", default="2b2t.org", help="адрес сервера (по умолчанию: %(default)s)"
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=30.0,
        help=f"секунд между проверками, не меньше {MIN_INTERVAL:g} "
        "(по умолчанию: %(default)s)",
    )
    parser.add_argument(
        "--no-queue",
        action="store_true",
        help="не запрашивать очередь у api.2b2t.vc (например, для другого сервера)",
    )
    parser.add_argument(
        "--proxy",
        action="append",
        default=[],
        metavar="URL",
        help="прокси вида socks5h://user:pass@host:port, socks5://, socks4://, "
        "http://; можно указать несколько раз",
    )
    parser.add_argument(
        "--proxy-file",
        action="append",
        default=[],
        type=Path,
        metavar="FILE",
        help="файл со списком прокси, по одному на строку (# — комментарий)",
    )
    parser.add_argument(
        "--proxy-cooldown",
        type=float,
        default=300.0,
        metavar="SEC",
        help="сколько не трогать сломавшийся прокси (по умолчанию: %(default)s)",
    )
    parser.add_argument("--no-color", action="store_true", help="без цвета")
    parser.add_argument(
        "--once", action="store_true", help="проверить один раз и выйти"
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    if args.interval < MIN_INTERVAL:
        parser.error(f"--interval должен быть не меньше {MIN_INTERVAL:g} секунд")
    return args


@dataclass
class Check:
    status: Status | None
    queue: Queue | None
    event: Event | None
    proxies_failed: bool


def check_once(
    address: tuple[str, int],
    want_queue: bool,
    detector: RestartDetector,
    pool: ProxyPool | None = None,
) -> Check:
    proxies_failed = False
    event = None
    try:
        status: Status | None = ping(address, pool)
    except AllProxiesFailed:
        # Says nothing about the server, so don't feed it to the detector.
        status = None
        proxies_failed = True
    except (OSError, ValueError):
        status = None
        event = detector.update(False)
    else:
        event = detector.update(True)

    queue = None
    if want_queue:
        try:
            queue = fetch_queue(pool)
        except Exception:
            queue = None
    return Check(status, queue, event, proxies_failed)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    colour = not args.no_color and sys.stdout.isatty()
    colorama.just_fix_windows_console()

    try:
        proxies = load_proxies(args.proxy, args.proxy_file)
    except (OSError, ValueError) as e:
        print(f"Ошибка в списке прокси: {e}", file=sys.stderr)
        return 2
    pool = ProxyPool(proxies, cooldown=args.proxy_cooldown) if proxies else None

    address = resolve(args.host)
    print(f"Moneymod Lite {__version__} by BG — слежу за {args.host}")
    if pool:
        print(f"Через прокси: {len(proxies)} шт.")
    detector = RestartDetector()
    try:
        while True:
            check = check_once(address, not args.no_queue, detector, pool)
            status, event = check.status, check.event
            line = format_line(
                datetime.now(),
                status,
                check.queue,
                detector.phase,
                check.proxies_failed,
            )
            if colour:
                line = (
                    colour_for(status, detector.phase, check.proxies_failed)
                    + line
                    + colorama.Style.RESET_ALL
                )
            print(line, flush=True)

            if event is Event.WENT_DOWN:
                print("Сервер ушёл на рестарт.", flush=True)
            elif event is Event.RESTART_FINISHED:
                message = "Рестарт прошёл! Сервер снова отвечает."
                if colour:
                    message = colorama.Style.BRIGHT + message + colorama.Style.RESET_ALL
                print("\a" + message, flush=True)

            if args.once:
                return 0 if status is not None else 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
