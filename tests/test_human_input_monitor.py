from __future__ import annotations

from human_input_monitor import HumanInputMonitor
from core_capabilities import core_human_input_monitor_descriptors


class FakeTakeover:
    def __init__(self):
        self.state = "agent"
        self.begin_calls = []

    def status(self):
        return {"state": self.state}

    def intervene(self, reason, provider_ids):
        self.begin_calls.append((reason, list(provider_ids)))
        self.state = "human"
        return {"state": "human"}


def _before(provider="computer", correlation_id="corr-1"):
    return {
        "event_type": "capability.before_invoke",
        "provider_id": provider,
        "correlation_id": correlation_id,
    }


def _done(provider="computer", correlation_id="corr-1"):
    return {
        "event_type": "capability.succeeded",
        "provider_id": provider,
        "correlation_id": correlation_id,
    }


def test_monitor_arms_only_for_interactive_providers():
    takeover = FakeTakeover()
    monitor = HumanInputMonitor(takeover, enabled=True)

    ignored = monitor.observer({
        "event_type": "capability.before_invoke",
        "provider_id": "core",
        "correlation_id": "x",
    })
    assert ignored["status"] == "ignored"
    assert monitor.is_armed() is False

    armed = monitor.observer(_before("browser"))
    assert armed["status"] == "armed"
    assert armed["active_count"] == 1
    assert monitor.is_armed() is True


def test_injected_input_is_ignored_and_physical_keyboard_triggers_takeover():
    takeover = FakeTakeover()
    monitor = HumanInputMonitor(takeover, enabled=True)
    monitor.observer(_before("computer"))

    assert monitor.note_input("keyboard", injected=True) is False
    assert monitor._perform_requested_takeover() is False
    assert takeover.begin_calls == []

    assert monitor.note_input("keyboard", injected=False) is True
    assert monitor._perform_requested_takeover() is True
    assert takeover.state == "human"
    assert takeover.begin_calls == [
        (
            "Physical user input detected during interactive automation",
            ["browser", "computer"],
        )
    ]


def test_physical_mouse_move_requires_threshold():
    takeover = FakeTakeover()
    monitor = HumanInputMonitor(
        takeover,
        enabled=True,
        mouse_move_threshold=10,
    )
    monitor.observer(_before("computer"))

    assert monitor.note_input(
        "mouse_move", injected=False, x=100, y=100
    ) is False
    assert monitor.note_input(
        "mouse_move", injected=False, x=105, y=105
    ) is False
    assert monitor.note_input(
        "mouse_move", injected=False, x=112, y=100
    ) is True


def test_mouse_button_and_wheel_trigger_immediately():
    for kind in ("mouse_button", "mouse_wheel"):
        takeover = FakeTakeover()
        monitor = HumanInputMonitor(takeover, enabled=True)
        monitor.observer(_before("browser"))
        assert monitor.note_input(kind, injected=False) is True
        assert monitor._perform_requested_takeover() is True


def test_completion_keeps_short_linger_window_armed():
    takeover = FakeTakeover()
    monitor = HumanInputMonitor(
        takeover,
        enabled=True,
        linger_seconds=5.0,
    )
    monitor.observer(_before("browser"))
    completed = monitor.observer(_done("browser"))
    assert completed["active_count"] == 0
    assert completed["status"] == "armed"
    assert monitor.is_armed() is True


def test_existing_takeover_is_strengthened_by_detected_intervention():
    takeover = FakeTakeover()
    monitor = HumanInputMonitor(takeover, enabled=True)
    monitor.observer(_before("computer"))
    assert monitor.note_input("keyboard", injected=False) is True

    takeover.state = "human"
    assert monitor._perform_requested_takeover() is True
    assert takeover.begin_calls == [
        (
            "Physical user input detected during interactive automation",
            ["browser", "computer"],
        )
    ]


def test_status_does_not_expose_raw_input_payloads():
    takeover = FakeTakeover()
    monitor = HumanInputMonitor(takeover, enabled=True)
    monitor.observer(_before("computer"))
    monitor.note_input("keyboard", injected=False)
    status = monitor.status()

    assert status["last_trigger_kind"] == "keyboard"
    assert "key" not in status
    assert "text" not in status
    assert "coordinates" not in status


def test_monitor_status_descriptor_is_read_only():
    descriptors = core_human_input_monitor_descriptors()
    assert len(descriptors) == 1
    descriptor = descriptors[0]
    assert descriptor.id == "core.human_input_monitor_status"
    assert descriptor.risk_level == "read"
    assert descriptor.requires_confirmation is False


def test_stop_signal_never_performs_pending_takeover():
    takeover = FakeTakeover()
    monitor = HumanInputMonitor(takeover, enabled=True)
    monitor.observer(_before("computer"))
    assert monitor.note_input("keyboard", injected=False) is True
    monitor._stop_requested.set()
    assert monitor._stop_requested.is_set() is True
    assert takeover.begin_calls == []
