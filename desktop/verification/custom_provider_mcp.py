"""RC.7 source-mode end-to-end: user-provided pinned Python MCP.

All installation paths, module code, receipts and runtime configuration live in
fresh Windows Temp directories. No existing user resources or credentials.
"""
import argparse
import json
from pathlib import Path
import socket
import tempfile
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from desktop_runtime.manager import Manager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    data = Path(tempfile.mkdtemp(prefix="pla-custom-provider-")).resolve()
    manager = Manager(data, args.resources.resolve())
    report = {"data": str(data), "resource": str(args.resources.resolve()),
              "mode": "source_manager_real_custom_package", "checks": [], "result": "INCOMPLETE"}

    def check(name, ok, evidence):
        report["checks"].append({"check": name, "status": "PASS" if ok else "FAIL",
                                  "evidence": evidence})
        assert ok, f"{name}: {evidence}"
        print(f"PASS {name}", flush=True)

    try:
        root = manager.components.root
        (root / "custom_mcp_server.py").write_text(
            'from fastmcp import FastMCP\n'
            'mcp = FastMCP("PLA isolated custom acceptance")\n'
            '@mcp.tool\n'
            'def hello() -> dict:\n'
            '    return {"proof": "PLA_RC7_CUSTOM_MCP_OK"}\n'
            'mcp.run()\n', encoding="utf-8")
        manifest = {"schema_version": 1, "id": "custom-acceptance", "autostart": False,
                    "runtime": {"kind": "isolated_python_stdio",
                                "python": ".provider_envs/custom-acceptance/Scripts/python.exe",
                                "args": ["-m", "custom_mcp_server"], "cwd": ".",
                                "discovery_timeout_seconds": 90},
                    "tool_allowlist": ["hello"],
                    "tool_overrides": {"hello": {"risk_level": "read", "requires_confirmation": False}}}
        content = json.dumps(manifest)
        preview = manager.dispatch("provider_import", {"content": content, "confirmed": False,
                                                        "expected_sha256": None})
        manager.dispatch("provider_import", {"content": content, "confirmed": True,
                                             "expected_sha256": preview["sha256"]})
        check("separate_custom_manifest", next(p for p in manager.dispatch("provider_catalog", {})["providers"] if p["provider_id"] == "custom-acceptance")["requested_enabled"] is False,
              "User manifest starts disabled")
        req = {"provider_id": "custom-acceptance", "package_kind": "python",
               "packages": ["fastmcp==4.0.3", "mcp==2.2.0"],
               "confirmed": False, "expected_sha256": None}
        plan = manager.dispatch("provider_package", req)
        manager.dispatch("provider_package", {**req, "confirmed": True,
                                              "expected_sha256": plan["sha256"]})
        check("pinned_custom_spec_registered", next(p for p in manager.dispatch("provider_catalog", {})["providers"] if p["provider_id"] == "custom-acceptance")["install_source"] == "user_custom",
              "Private reviewed spec")
        install_req = {"provider_id": "custom-acceptance", "skill_package": None,
                       "confirmed": False, "expected_sha256": None}
        install_preview = manager.dispatch("provider_install", install_req)
        started = manager.dispatch("provider_install", {**install_req, "confirmed": True,
                                                         "expected_sha256": install_preview["sha256"]})
        check("actual_private_installer_started", started.get("started") is True, started.get("job"))
        deadline = time.monotonic() + 210
        while True:
            jobs = manager.dispatch("installation_status", {})
            job = next(job for job in jobs["jobs"] if job["provider_id"] == "custom-acceptance")
            if job["state"] != "running":
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Isolated private Python MCP installation did not finish")
            time.sleep(1)
        check("actual_private_installer_exit", job["exit_code"] == 0 and job["state"] == "installed",
              {"exit_code": job["exit_code"], "state": job["state"], "logs": job["logs"][-8:]})
        python = root / ".provider_envs/custom-acceptance/Scripts/python.exe"
        check("isolated_python_available", python.is_file(), str(python))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        manager.config.save({"runtime_port": port})
        manager.start()
        pid = manager.children["runtime"].pid
        response = manager.dispatch("provider_action", {"action": "enable",
                                                          "provider_id": "custom-acceptance",
                                                          "confirmed": True})
        check("custom_provider_live", response["data"]["provider"]["state"] == "ready",
              response["data"]["provider"])
        outcome = manager._invoke_management_capability("custom-acceptance.hello", {})
        check("custom_tool_call_real", "PLA_RC7_CUSTOM_MCP_OK" in json.dumps(outcome), outcome.get("data"))
        manager.dispatch("provider_action", {"action": "disable", "provider_id": "custom-acceptance",
                                              "confirmed": True})
        check("runtime_unchanged", manager.children["runtime"].pid == pid, pid)
        check("development_permission_not_required",
              manager.dispatch("development_status", {})["enabled"] is False, "pla-development disabled")
        report["result"] = "PASS"
    except Exception as exc:
        report["result"] = "FAIL"
        report["error"] = str(exc)
        raise
    finally:
        manager.stop()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"result": report["result"], "report": str(args.report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
