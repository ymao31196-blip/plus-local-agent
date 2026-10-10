"""RC.8 all eleven MCP providers in one isolated Desktop Runtime.

Reuses only a marked independent temporary RC.7 starter fixture containing
seven real package environments. Installs user-reviewed WPS and Skill Library
into that same fixture, discovers official WinGet and the bundled Browser.
Never touches the normal Desktop data root, credentials or user's office files.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from desktop_runtime.manager import Manager

ALL = (
    "browser", "computer", "docx", "markitdown", "office-enhancement",
    "pdf", "skill-library", "software-migration", "windows-management",
    "winget", "wps-office",
)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--skill-package", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    data = args.data.resolve(strict=True)
    assert data.name.startswith("pla-rc7-starter-")
    assert data.parent == (Path.home() / "AppData/Local/Temp").resolve()
    resources = args.resources.resolve(strict=True)
    manager = Manager(data, resources)
    report = {"mode": "source_rc8_complete_runtime", "data": str(data),
              "checks": [], "providers": {}, "status": "INCOMPLETE"}

    def check(name, condition, evidence=None):
        report["checks"].append({"test": name, "ok": bool(condition), "evidence": evidence})
        assert condition, f"{name}: {evidence}"
        print("PASS " + name, flush=True)

    def install(provider_id, package=None):
        req = {"provider_id": provider_id, "skill_package": str(package) if package else None,
               "confirmed": False, "expected_sha256": None}
        preview = manager.dispatch("provider_install", req)
        job = manager.dispatch("provider_install", {**req, "confirmed": True,
                                                   "expected_sha256": preview["sha256"]})
        check("start_install_" + provider_id, job.get("started") is True, job.get("job"))
        end = time.monotonic() + 220
        while True:
            jobs = manager.dispatch("installation_status", {})["jobs"]
            item = next(x for x in jobs if x["provider_id"] == provider_id)
            if item["state"] != "running":
                break
            if time.monotonic() >= end:
                raise TimeoutError(provider_id + " install timed out")
            time.sleep(1)
        check("install_receipt_" + provider_id, item["state"] == "installed" and item["exit_code"] == 0,
              {"state": item["state"], "exit_code": item["exit_code"],
               "tail": item.get("logs", [])[-4:]})

    try:
        receipts = manager.components.root / "receipts"
        existing = [p.stem for p in receipts.glob("*.json")]
        check("seven_preinstalled_isolated_components", len(existing) >= 7,
              sorted(existing))
        install("skill-library", args.skill_package.resolve(strict=True))
        install("wps-office")
        runtime_port, browser_port, health_port = free_port(), free_port(), free_port()
        while len({runtime_port, browser_port, health_port}) != 3:
            runtime_port, browser_port, health_port = free_port(), free_port(), free_port()
        manager.config.save({"runtime_port": runtime_port, "browser_port": browser_port,
                             "health_port": health_port, "browser_enabled": True})
        manager.start()
        pid = manager.children["runtime"].pid
        for provider_id in ALL:
            item = {"provider_id": provider_id}
            try:
                result = manager.dispatch("provider_action", {
                    "action": "enable", "provider_id": provider_id, "confirmed": True})
                lifecycle = result.get("data", {}).get("provider", {})
                item["state"] = lifecycle.get("state")
                item["tool_count"] = lifecycle.get("tool_count", 0)
                item["ok"] = lifecycle.get("state") == "ready" and item["tool_count"] > 0
                if not item["ok"]:
                    item["error"] = lifecycle.get("error_message")
                else:
                    caps = manager.dispatch("provider_details", {"provider_id": provider_id})["capabilities"]
                    item["capability_count"] = len(caps)
            except Exception as exc:
                item["ok"] = False
                item["error"] = repr(exc)
            report["providers"][provider_id] = item
            print(("PASS " if item["ok"] else "FAIL ") + provider_id, flush=True)
        report["connected"] = sum(x["ok"] for x in report["providers"].values())
        report["total_tools"] = sum(x.get("tool_count", 0) for x in report["providers"].values())
        check("all_eleven_ready", report["connected"] == len(ALL), report["providers"])
        check("runtime_pid_unchanged", manager.children["runtime"].pid == pid, pid)
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = repr(exc)
        raise
    finally:
        for name in reversed(ALL):
            try:
                if manager._alive("runtime"):
                    manager.dispatch("provider_action", {
                        "action": "disable", "provider_id": name, "confirmed": True})
            except Exception:
                pass
        manager.stop()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"],
                          "connected": report.get("connected"), "total_tools": report.get("total_tools"),
                          "report": str(args.report)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
