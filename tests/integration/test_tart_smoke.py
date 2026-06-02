"""Integration tests — require real Tart/VM when enabled."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MACBOX_RUN_INTEGRATION") != "1",
    reason="Set MACBOX_RUN_INTEGRATION=1 to run integration tests",
)


def test_doctor_integration() -> None:
    from click.testing import CliRunner

    from macbox.cli import main

    result = CliRunner().invoke(main, ["doctor", "--json"])
    assert result.output
