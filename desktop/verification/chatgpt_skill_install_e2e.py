"""Skill Library v0.5.0 installed through ChatGPT's Desktop Capability Broker.

Uses existing user-owned reviewed source, but installs only to fresh Temp data,
without editing the source repository, the installed Desktop or its credentials.
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
    parser.add_argument("--skill-package", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    data = Path(tempfile.mkdtemp(prefix="pla-skill-native-")).resolve()
    manager = Manager(data, args.resources.resolve())
    report = {"mode": "source_runtime_chatgpt_skill_install", "data": str(data),
              "tests": [], "status": "INCOMPLETE"}
    def check(name, passed, detail=None):
        report["tests"].append({"test": name, "passed": bool(passed), "evidence": detail})
        assert passed, f"{name}: {detail}"
        print("PASS " + name, flush=True)
    def invoke(name, params=None, confirmed=False):
        return manager._invoke_management_capability(name, params or {}, confirmed)["data"]
    try:
        package = str(args.skill_package.resolve(strict=True))
        sources = data / "workspace" / "skill-tests"
        skill = sources / "skills" / "rc7-skill-proof" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        content = "---\nname: rc7-skill-proof\ndescription: Isolated install proof\n---\n\n# RC7 Skill proof\nRead this test Skill.\n"
        skill.write_text(content, encoding="utf-8")
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        manager.config.save({"runtime_port": port})
        manager.dispatch("skill_permissions", {"local_roots": ["workspace"], "writable_roots": ["workspace"], "confirmed": True})
        manager.start()
        pid = manager.children["runtime"].pid
        # The original rc.7 ChatGPT preview has no skill_package argument.
        # The repaired bridge must now produce a SHA-bound source-specific plan.
        plan = invoke("runtime.desktop_install_preview", {"provider_id": "skill-library", "skill_package": package})
        check("skill_source_bound_plan", plan["skill_package"] == package and bool(plan["skill_package_sha256"]), plan.get("skill_package_sha256"))
        try:
            invoke("runtime.provider_setup", {"provider_id": "skill-library", "skill_package": package, "expected_sha256": "0" * 64}, True)
            raise AssertionError("Stale installation preview accepted")
        except Exception as e:
            check("stale_hash_rejected", "changed" in str(e) or "preview" in str(e), str(e)[:150])
        job = invoke("runtime.provider_setup", {"provider_id": "skill-library",
                        "skill_package": package, "expected_sha256": plan["sha256"]}, True)
        check("privileged_component_install_queued", job.get("status") == "started", job.get("job"))
        deadline = time.monotonic() + 260
        while True:
            status = invoke("runtime.desktop_install_status")
            if status["state"] != "running":
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Skill installation still running")
            time.sleep(1)
        check("real_skill_install_receipt", status["state"] == "installed" and status["exit_code"] == 0,
              {"state": status["state"], "code": status["exit_code"],
               "log": status.get("logs_tail", "")[-1200:]})
        enabled = invoke("runtime.provider_enable", {"provider_id": "skill-library"}, True)
        check("seventeen_real_skill_capabilities", enabled["provider"]["state"] == "ready"
              and enabled["provider"]["tool_count"] == 17, enabled["provider"])
        def skill_rpc(action, params=None):
            result = manager.dispatch("skill_action", {"action": action, "arguments": params or {}, "confirmed": False})
            assert result.get("status") == "completed" and not result.get("is_error"), result
            return result["data"]
        skill_rpc("manage", {"action": "add", "source_id": "rc7test", "kind": "local", "location": str(sources)})
        skill_rpc("sync", {"source_id": "rc7test"})
        found = skill_rpc("find", {"query": "rc7-skill-proof", "include_best_content": True})
        check("skill_search_real", "rc7-skill-proof" in json.dumps(found), str(found)[:450])
        loaded = skill_rpc("load", {"name": "rc7test:rc7-skill-proof"})
        check("skill_content_read_real", loaded.get("content") == content, loaded.get("content", "")[:100])
        invoke("runtime.provider_disable", {"provider_id": "skill-library"}, True)
        check("runtime_pid_unchanged", manager.children["runtime"].pid == pid, pid)
        report["status"] = "PASS"
    except Exception as e:
        report["status"] = "FAIL"
        report["error"] = repr(e)
        raise
    finally:
        manager.stop()
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(args.report)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
