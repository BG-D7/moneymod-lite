import pytest

from moneymod_lite.detector import Event, Phase, RestartDetector


def test_starts_unknown_then_up() -> None:
    detector = RestartDetector()
    assert detector.phase is Phase.UNKNOWN
    assert detector.update(True) is None
    assert detector.phase is Phase.UP


def test_single_failure_is_not_a_restart() -> None:
    detector = RestartDetector(failures_to_down=2)
    detector.update(True)
    assert detector.update(False) is None
    assert detector.phase is Phase.UP
    assert detector.update(True) is None


def test_restart_cycle() -> None:
    detector = RestartDetector(failures_to_down=2)
    detector.update(True)
    assert detector.update(False) is None
    assert detector.update(False) is Event.WENT_DOWN
    assert detector.phase is Phase.DOWN
    # Staying down doesn't repeat the event.
    assert detector.update(False) is None
    assert detector.update(True) is Event.RESTART_FINISHED
    assert detector.phase is Phase.UP
    assert detector.update(True) is None


def test_started_while_server_is_down() -> None:
    detector = RestartDetector(failures_to_down=1)
    assert detector.update(False) is Event.WENT_DOWN
    assert detector.update(True) is Event.RESTART_FINISHED


def test_rejects_bad_threshold() -> None:
    with pytest.raises(ValueError):
        RestartDetector(failures_to_down=0)
