"""Evidence-based Tunnel status for the pinned client's Prometheus metrics."""
from __future__ import annotations

import math
import re


def metric_values(text: str, name: str) -> list[float]:
    pattern = re.compile(r"^" + re.escape(name) + r"(?:\{[^\n]*\})?\s+([^\s]+)(?:\s|$)")
    result = []
    for line in text.splitlines():
        matched = pattern.match(line)
        if matched:
            try:
                value = float(matched.group(1))
                if math.isfinite(value):
                    result.append(value)
            except ValueError:
                pass
    return result


class TunnelReadiness:
    """A local readiness response alone must never assert remote connectivity."""
    def __init__(self):
        self.errors = 0.0
        self.success_at_error = 0.0
        self.last_success = 0.0

    def observe(self, local_ready: bool, metrics: str, now: float) -> str:
        successes = metric_values(metrics, "commands_poll_last_successful_timestamp_seconds")
        success = max(successes, default=0.0)
        errors = sum(metric_values(metrics, "commands_poll_errors_total"))
        if errors > self.errors:
            self.success_at_error = success
        self.errors = errors
        self.last_success = success
        if not local_ready:
            return "starting_or_disconnected"
        if success > 0 and success > self.success_at_error and -5 <= now - success <= 60:
            return "ready"
        if errors > 0 or success > 0:
            return "disconnected"
        return "local_ready_waiting_control_plane"
