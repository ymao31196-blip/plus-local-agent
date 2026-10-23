"""Validate all seven installed starter-pack MCP services on a private Desktop Runtime.

Does not call any write-capable remote tool and never grants user workspaces.
"""
import argparse
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from desktop_runtime.manager import Manager
from desktop_runtime.installer import CORE_BUNDLE_IDS


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--resources", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    manager = Manager(a.data.resolve(strict=True), a.resources.resolve(strict=True))
    report = {"data": str(a.data), "status": "INCOMPLETE", "providers": {}}
    try:
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        manager.config.save({"runtime_port": port})
        manager.start()
        pid = manager.children["runtime"].pid
        for provider_id in CORE_BUNDLE_IDS:
            row = {"provider_id": provider_id}
            try:
                enabled = manager.dispatch("provider_action",
                    {"action": "enable", "provider_id": provider_id, "confirmed": True})
                lifecycle = enabled.get("data", {}).get("provider") or {}
                row["state"] = lifecycle.get("state")
                row["tool_count"] = lifecycle.get("tool_count")
                row["error_message"] = lifecycle.get("error_message")
                row["passed"] = lifecycle.get("state") == "ready" and (lifecycle.get("tool_count") or 0) > 0
                if row["passed"]:
                    details = manager.dispatch("provider_details", {"provider_id": provider_id})
                    row["available_tools"] = sum(c.get("available", False) for c in details.get("capabilities", []))
                    row["passed"] = row["available_tools"] > 0
            except Exception as exc:
                row["passed"] = False
                row["error"] = repr(exc)
            finally:
                try:
                    manager.dispatch("provider_action",
                        {"action": "disable", "provider_id": provider_id, "confirmed": True})
                except Exception as exc:
                    row["disable_error"] = repr(exc)
            print(("PASS " if row["passed"] else "FAIL ") + provider_id, flush=True)
            report["providers"][provider_id] = row
        report["runtime_pid_unchanged"] = manager.children["runtime"].pid == pid
        report["status"] = "PASS" if all(r["passed"] for r in report["providers"].values()) and report["runtime_pid_unchanged"] else "PARTIAL"
    finally:
        manager.stop()
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"status": report["status"], "passed": sum(r["passed"] for r in report["providers"].values()),
                          "total": len(CORE_BUNDLE_IDS), "report": str(a.report)}), flush=True)


if __name__ == "__main__":
    main()
