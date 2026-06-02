"""Tests for run management and crash detection."""

from macbox.runs import RunManager, detect_new_crashes, list_crash_basenames


def test_run_directory_created_under_state_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MACBOX_STATE_DIR", str(tmp_path))
    manager = RunManager.from_config()
    metadata = manager.create_run("macbox-test-001", "macos-sequoia-clean")

    run_dir = manager.run_dir(metadata.run_id)
    assert run_dir.is_dir()
    assert (run_dir / "screenshots").is_dir()
    assert (run_dir / "logs").is_dir()
    assert (run_dir / "crashes").is_dir()
    assert (run_dir / "metadata.json").is_file()


def test_crash_detection() -> None:
    before = {"App_2026.crash", "Old.ips"}
    after = {"App_2026.crash", "Old.ips", "MyApp_2026.crash"}
    new = detect_new_crashes(before, after)
    assert new == ["MyApp_2026.crash"]


def test_list_crash_basenames_filters() -> None:
    names = ["foo.log", "bar.crash", "baz.ips", "qux.panic", "readme.txt"]
    assert list_crash_basenames(names) == {"bar.crash", "baz.ips", "qux.panic"}
