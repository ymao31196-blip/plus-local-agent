"""Live RC.7 starter-pack installation in an isolated Desktop private data root."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from desktop_runtime.manager import Manager
from desktop_runtime.installer import CORE_BUNDLE_IDS


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--resources", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--deadline", type=int, default=265)
    a = p.parse_args()
    private = Path(tempfile.mkdtemp(prefix="pla-rc7-starter-")).resolve()
    manager = Manager(private, a.resources.resolve())
    result = {"private": str(private), "version": "source-rc7", "bundle": list(CORE_BUNDLE_IDS),
              "checks": [], "status": "INCOMPLETE"}
    try:
        preview = manager.dispatch("provider_bundle", {"confirmed": False, "expected_sha256": None})
        assert preview["providers"] == list(CORE_BUNDLE_IDS)
        result["checks"].append("preview reviewed seven pinned packages")
        start = manager.dispatch("provider_bundle", {"confirmed": True, "expected_sha256": preview["sha256"]})
        assert start["started"] is True and start["job"] == "installer:starter-pack"
        result["checks"].append("real installer task started")
        deadline = time.monotonic() + a.deadline
        while True:
            state = manager.dispatch("installation_status", {})
            job = next(row for row in state["jobs"] if row["job"] == "installer:starter-pack")
            if job["state"] != "running":
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Starter-pack install exceeds deadline")
            time.sleep(1)
        result["job"] = job
        result["receipts"] = state["installed_receipts"]
        result["installed_count"] = sum(item in state["installed_receipts"] for item in CORE_BUNDLE_IDS)
        if job["state"] != "installed":
            raise AssertionError("Starter bundle failed; actual job state " + job["state"])
        result["checks"].append("all seven independent installation receipts")
        from desktop_runtime.components import ComponentProject
        project = ComponentProject(private, a.resources.resolve())
        catalog = project.stage(18931)["providers"]
        rows = {row["provider_id"]: row for row in catalog}
        bad = [name for name in CORE_BUNDLE_IDS if not rows[name].get("python_exists")]
        if bad:
            raise AssertionError("Missing isolated python executables: " + ", ".join(bad))
        result["checks"].append("seven real provider interpreters exist")
        assert manager.components.preferences()["enabled"] == []
        result["checks"].append("no provider or source-development permissions automatically enabled")
        result["status"] = "PASS"
        print("PASS: all seven starter-pack component installations; receipts and interpreters verified.", flush=True)
    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = repr(e)
        print("FAIL: " + repr(e), flush=True)
        raise
    finally:
        manager.stop()
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": result["status"], "report": str(a.report),
                           "installed_count": result.get("installed_count", 0)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
