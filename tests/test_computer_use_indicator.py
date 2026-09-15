from __future__ import annotations

import json

import computer_use_indicator as indicator


def _event(event_type: str, *, risk_level: str | None = None, correlation_id: str = "c1") -> dict:
    payload = {}
    if risk_level is not None:
        payload["risk_level"] = risk_level
    return {
        "event_type": event_type,
        "provider_id": "computer",
        "capability_id": "computer.click" if risk_level != "read" else "computer.inspect",
        "correlation_id": correlation_id,
        "payload": payload,
    }


def test_non_computer_events_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(indicator, "STATE_DIR", tmp_path)
    monkeypatch.setattr(indicator, "STATE_PATH", tmp_path / "state.json")
    event = _event("capability.before_invoke", risk_level="read")
    event["provider_id"] = "browser"

    result = indicator.computer_use_indicator_observer(event)

    assert result["status"] == "ignored"
    assert not indicator.STATE_PATH.exists()


def test_control_event_lifecycle_updates_state(tmp_path, monkeypatch):
    monkeypatch.setattr(indicator, "STATE_DIR", tmp_path)
    monkeypatch.setattr(indicator, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(indicator.os, "name", "nt")
    monkeypatch.setattr(indicator, "_ensure_indicator_process", lambda: None)

    before = indicator.computer_use_indicator_observer(
        _event("capability.before_invoke", risk_level="write_external")
    )
    active = json.loads(indicator.STATE_PATH.read_text(encoding="utf-8"))

    assert before == {"status": "updated", "mode": "control", "active_count": 1}
    assert active["mode"] == "control"
    assert "c1" in active["inflight"]
    assert active["linger_until"] == 0.0

    done = indicator.computer_use_indicator_observer(
        _event("capability.succeeded")
    )
    linger = json.loads(indicator.STATE_PATH.read_text(encoding="utf-8"))

    assert done == {"status": "updated", "mode": "control", "active_count": 0}
    assert linger["inflight"] == {}
    assert linger["linger_until"] > linger["updated_at"]


def test_read_event_uses_observation_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(indicator, "STATE_DIR", tmp_path)
    monkeypatch.setattr(indicator, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(indicator.os, "name", "nt")
    monkeypatch.setattr(indicator, "_ensure_indicator_process", lambda: None)

    result = indicator.computer_use_indicator_observer(
        _event("capability.before_invoke", risk_level="read")
    )
    state = json.loads(indicator.STATE_PATH.read_text(encoding="utf-8"))

    assert result["mode"] == "observe"
    assert state["mode"] == "observe"


def test_control_mode_wins_when_calls_overlap(tmp_path, monkeypatch):
    monkeypatch.setattr(indicator, "STATE_DIR", tmp_path)
    monkeypatch.setattr(indicator, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(indicator.os, "name", "nt")
    monkeypatch.setattr(indicator, "_ensure_indicator_process", lambda: None)

    indicator.computer_use_indicator_observer(
        _event("capability.before_invoke", risk_level="read", correlation_id="read")
    )
    result = indicator.computer_use_indicator_observer(
        _event(
            "capability.before_invoke",
            risk_level="write_external",
            correlation_id="write",
        )
    )

    assert result["mode"] == "control"
    assert result["active_count"] == 2
