import re
from pathlib import Path

from moneymod_lite import __version__


def test_version_matches_pyproject() -> None:
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match is not None
    assert match.group(1) == __version__
