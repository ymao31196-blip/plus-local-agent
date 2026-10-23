"""RC.7+/RC.8 optional providers: real read-only WPS and official WinGet tests.

No user files, WPS document modifications or package installation actions.
Run against a temp WPS installation or fresh isolated Windows App Installer MCP.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import socket
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from desktop_runtime.manager import Manager
from desktop_runtime.system_components import official_winget_mcp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--mode", choices=["wps", "winget"], required=True)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    data = args.data.resolve(strict=True) if args.data else Path(tempfile.mkdtemp(prefix="pla-rc8-system-mcp-")).resolve()
    manager = Manager(data, args.resources.resolve(strict=True))
    report = {"provider": args.mode, "data": str(data), "status": "INCOMPLETE", "checks": []}

    def record(key, ok, evidence):
        report["checks"].append({"check": key, "passed": bool(ok), "evidence": evidence})
        print(("PASS " if ok else "FAIL ") + key, flush=True)
        return ok

    provider = "wps-office" if args.mode == "wps" else "winget"
    try:
        if args.mode == "winget":
            candidate = official_winget_mcp()
            record("official_app_installer_path", candidate is not None, str(candidate))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        manager.config.save({"runtime_port": port})
        manager.start()
        process = manager.children["runtime"].pid
        catalog = manager.dispatch("provider_catalog", {})["providers"]
        row = next(item for item in catalog if item["provider_id"] == provider)
        record("managed_manifest_available", row["execution_files_present"], {
            "command": row.get("command"), "cwd": row.get("cwd"),
            "directory_exists": row.get("directory_exists"),
            "execution_files_present": row.get("execution_files_present")})
        enabled = manager.dispatch("provider_action", {"action": "enable", "provider_id": provider, "confirmed": True})
        lifecycle = enabled["data"]["provider"]
        record("mcp_live_ready", lifecycle.get("state") == "ready", lifecycle)
        if lifecycle.get("state") == "ready":
            details = manager.dispatch("provider_details", {"provider_id": provider})["capabilities"]
            report["tools"] = [{"id": t["id"], "input_schema": t.get("input_schema"), "available": t["available"]}
                               for t in details if t.get("available") and (
                                   t["id"].endswith("wps_common_ping") or
                                   t["id"].endswith("wps_check_connection") or
                                   t["id"].endswith("find-winget-packages"))]
            record("real_tool_descriptors_found", bool(report["tools"]), report["tools"])
            if args.mode == "wps":
                for suffix in ("wps_common_ping", "wps_check_connection"):
                    capability = f"wps-office.{suffix}"
                    try:
                        actual = manager._invoke_management_capability(capability, {})
                        record("readonly_" + suffix + "_invoked",
                               actual.get("status") == "completed" and not actual.get("is_error"),
                               {key: str(actual.get(key))[:1800] for key in ("status", "data", "content")})
                    except Exception as exc:
                        record("readonly_" + suffix + "_invoked", False, str(exc)[:1200])
            else:
                try:
                    result = manager._invoke_management_capability(
                        "winget.find-winget-packages", {"query": "Git", "upgradeable": False})
                    record("readonly_winget_search_invoked",
                           result.get("status") == "completed" and not result.get("is_error"),
                           {key: str(result.get(key))[:2000]
                            for key in ("status", "data", "content")})
                except Exception as exc:
                    record("readonly_winget_search_invoked", False, str(exc)[:1500])
                report["read_only_probe"] = "Only find-winget-packages invoked; never install-winget-package"
        report["status"] = "PASS" if all(item["passed"] for item in report["checks"]) else "PARTIAL"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = repr(exc)
        print("ERROR " + repr(exc), flush=True)
    finally:
        try:
            manager.dispatch("provider_action", {"action": "disable", "provider_id": provider, "confirmed": True})
        except Exception as exc:
            report["disable_error"] = repr(exc)
        if "process" in locals():
            report["main_runtime_unchanged"] = manager.children.get("runtime") is not None and manager.children["runtime"].pid == process
        manager.stop()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(args.report)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
