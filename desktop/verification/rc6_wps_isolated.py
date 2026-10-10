"""Isolated installed-runtime WPS Provider acceptance for PLA Desktop RC.6.

No GUI installer, no default Desktop data, no Tunnel credentials. Uses the
real frozen manager and the reviewed pinned third-party source spec.
"""
from __future__ import annotations
import argparse
import json
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from components_mcp import FrozenManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    resources = args.resources.resolve(strict=True)
    data = Path(tempfile.mkdtemp(prefix="pla-desktop-rc6-wps-")).resolve()
    report = {"mode": "frozen_manager_isolated_wps_provider", "data": str(data),
              "result": "INCOMPLETE", "checks": []}
    manager = FrozenManager(data, resources)

    def check(name, ok, detail):
        report["checks"].append({"check": name, "status": "PASS" if ok else "FAIL",
                                  "detail": detail})
        if not ok:
            raise AssertionError(name + ": " + str(detail))
        print("PASS " + name, flush=True)

    try:
        catalog = manager.dispatch("provider_catalog", {})["providers"]
        row = next(p for p in catalog if p["provider_id"] == "wps-office")
        check("reviewed_wps_manifest_discovered", row["install_supported"], row["provider_id"])
        check("missing_wps_source_blocks_enable", not row["directory_exists"], row["cwd"])
        try:
            manager.dispatch("provider_action", {"action": "enable", "provider_id": "wps-office", "confirmed": True})
        except Exception as err:
            check("preinstall_enable_rejected", "working directory missing" in str(err), str(err))
        else:
            raise AssertionError("WPS enabled without its working directory")
        args_install = {"provider_id": "wps-office", "skill_package": None,
                        "confirmed": False, "expected_sha256": None}
        plan = manager.dispatch("provider_install", args_install)
        check("reviewed_install_preview", plan["provider_id"] == "wps-office", plan["environment"])
        started = manager.dispatch("provider_install", {**args_install, "confirmed": True,
                                                           "expected_sha256": plan["sha256"]})
        check("real_installer_job_started", started.get("started") is True, started.get("job"))
        deadline = time.monotonic() + 250
        while True:
            status = manager.dispatch("installation_status", {})
            job = next(j for j in status["jobs"] if j["provider_id"] == "wps-office")
            if job["state"] != "running":
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Pinned WPS source installation exceeds 250s")
            time.sleep(1)
        report["installer"] = job
        report["installed_receipts"] = status["installed_receipts"]
        check("real_wps_installer_receipt", job["state"] == "installed", {"state": job["state"],
                                                                        "exit_code": job["exit_code"],
                                                                        "logs": job.get("logs", [])[-10:]})
        row = next(p for p in manager.dispatch("provider_catalog", {})["providers"]
                   if p["provider_id"] == "wps-office")
        check("wps_workdir_and_executable_present", row["execution_files_present"], row["cwd"])
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        manager.dispatch("configure", {"runtime_port": port})
        manager.start()
        try:
            outcome = manager.dispatch("provider_action", {"action": "enable", "provider_id": "wps-office",
                                                           "confirmed": True})
            report["enable_result"] = outcome
            check("wps_runtime_connected", outcome["data"]["provider"]["state"] == "ready",
                  outcome["data"]["provider"])
            tools = manager.dispatch("provider_details", {"provider_id": "wps-office"})
            check("wps_real_tool_capabilities_discovered",
                  any(c["available"] for c in tools["capabilities"]), len(tools["capabilities"]))
        finally:
            try:
                manager.dispatch("provider_action", {"action": "disable", "provider_id": "wps-office",
                                                      "confirmed": True})
            except Exception:
                pass
        report["result"] = "PASS"
    except Exception as exc:
        report["result"] = "FAIL"
        report["error"] = str(exc)
        report["manager_logs"] = "\n".join(manager.logs)[-10000:]
        raise
    finally:
        try:
            manager.stop()
        except Exception:
            pass
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"result": report["result"], "report": str(args.report)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
