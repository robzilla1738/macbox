"""Tests for macbox models."""

from macbox.models import MacboxResponse, make_run_id


def test_macbox_response_json_shape() -> None:
    response = MacboxResponse(
        ok=True,
        command="status",
        vm=None,
        data={"ready": True},
        warnings=[],
        errors=[],
    )
    payload = response.model_dump()
    assert set(payload.keys()) == {"ok", "command", "vm", "data", "warnings", "errors"}
    assert payload["ok"] is True
    assert payload["command"] == "status"


def test_make_run_id_format() -> None:
    run_id = make_run_id("macbox-test-001")
    assert run_id.endswith("-macbox-test-001")
    assert "T" in run_id
