"""Integration test configuration."""

import os

import pytest


def pytest_collection_modifyitems(config, items) -> None:
    if os.environ.get("MACBOX_RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="Set MACBOX_RUN_INTEGRATION=1 to enable integration tests")
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(skip)
