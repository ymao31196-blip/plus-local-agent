"""Live source Desktop Runtime test of ChatGPT-native component capabilities.

Only a private temporary data directory is used. No user install/credentials.
The test uses exactly the same stable capability_invoke broker tool as ChatGPT.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from desktop_runtime.manager import Manager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix="pla-chatgpt-rc7-")).resolve()
    manager = Manager(directory, args.resources.resolve())
    report = {"data": str(directory), "checks": [], "status": "INCOMPLETE"}
    def check(name, condition, evidence):
        report["checks"].append({"name": name, "passed": bool(condition), "evidence": evidence})
        assert condition, f"{name}: {evidence}"
        print("PASS " + name, flush=True)
    def invoke(name, arguments=None, confirm=False):
        result = manager._invoke_management_capability(name, arguments or {}, confirmed=confirm)
        return result["data"]
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        manager.config.save({"runtime_port": port})
        manager.start()
        runtime_pid = manager.children["runtime"].pid
        catalog = invoke("runtime.capability_catalog", {"provider_id": "runtime"})["capabilities"]
        present = {item["id"] for item in catalog}
        four = {"runtime.desktop_package_preview", "runtime.desktop_package_commit",
                "runtime.desktop_install_preview", "runtime.desktop_install_status"}
        check("actual_dynamic_broker_capabilities", four.issubset(present), sorted(present & four))
        # A real isolated custom stdio MCP whose implementation is restricted
        # to the new user-owned private component directory.
        components = directory / "components"
        components.mkdir(exist_ok=True)
        (components / "my_rc7_mcp.py").write_text(
            'from fastmcp import FastMCP\n'
            'mcp=FastMCP("RC7 ChatGPT native")\n'
            '@mcp.tool\n'
            'def ping()->dict:\n'
            '    return {"proof":"RC7_CHATGPT_INSTALL_OK"}\n'
            'mcp.run()\n', encoding="utf-8")
        manifest = json.dumps({"schema_version": 1, "id": "chatgpt-rc7",
                "runtime": {"kind": "isolated_python_stdio",
                            "python": ".provider_envs/chatgpt-rc7/Scripts/python.exe",
                            "args": ["-m", "my_rc7_mcp"], "cwd": ".",
                            "discovery_timeout_seconds": 90},
                "tool_allowlist": ["ping"],
                "tool_overrides": {"ping": {"risk_level": "read", "requires_confirmation": False}}})
        review = invoke("runtime.provider_import", {"content": manifest}, confirm=True)
        saved = invoke("runtime.provider_import", {"content": manifest, "confirm": True,
                                                   "expected_sha256": review["sha256"]}, confirm=True)
        check("provider_import_review_and_commit", saved["imported"] and not saved["launched"], saved["provider_id"])
        deps = {"provider_id": "chatgpt-rc7", "package_kind": "python",
                "packages": ["fastmcp==4.0.3", "mcp==2.2.0"]}
        p = invoke("runtime.desktop_package_preview", deps)
        q = invoke("runtime.desktop_package_commit", {**deps, "expected_sha256": p["sha256"]}, confirm=True)
        check("private_exact_dependency_spec_committed", q["committed"], q["spec_file"])
        plan = invoke("runtime.desktop_install_preview", {"provider_id": "chatgpt-rc7"})
        job = invoke("runtime.provider_setup",
                    {"provider_id": "chatgpt-rc7", "expected_sha256": plan["sha256"]}, confirm=True)
        check("privileged_real_setup_queued", job["status"] == "started", job)
        deadline = time.monotonic() + 200
        while True:
            status = invoke("runtime.desktop_install_status")
            if status["state"] != "running":
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("ChatGPT-native custom install timed out")
            time.sleep(1)
        check("installer_receipt_and_exit_code", status["state"] == "installed" and status["exit_code"] == 0,
              {"state": status["state"], "code": status["exit_code"], "tail": status["logs_tail"][-1200:]})
        enabled = invoke("runtime.provider_enable", {"provider_id": "chatgpt-rc7"}, confirm=True)
        check("provider_hotplug_ready", enabled["provider"]["state"] == "ready", enabled["provider"])
        tool = invoke("chatgpt-rc7.ping")
        check("actual_custom_mcp_tool_invoked", tool.get("proof") == "RC7_CHATGPT_INSTALL_OK", tool)
        invoke("runtime.provider_disable", {"provider_id": "chatgpt-rc7"}, confirm=True)
        check("main_runtime_unchanged", manager.children["runtime"].pid == runtime_pid, runtime_pid)
        check("no_pla_source_development_permission", manager.dispatch("development_status", {})["enabled"] is False, True)
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = repr(exc)
        raise
    finally:
        manager.stop()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(args.report)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
