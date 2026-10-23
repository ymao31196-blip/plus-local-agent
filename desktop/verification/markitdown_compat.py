"""Compatibility probe for legacy MarkItDown MCP with a pinned Pydantic version.

Mutates only the private temporary starter-pack acceptance environment.
"""
from pathlib import Path
import argparse
import hashlib
import json
import socket
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from desktop_runtime.manager import Manager
from desktop_runtime.installer import UV_SHA256, ComponentInstaller

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--data",type=Path,required=True)
    a.add_argument("--resources",type=Path,required=True)
    a.add_argument("--report",type=Path,required=True)
    x=a.parse_args()
    manager=Manager(x.data.resolve(strict=True),x.resources.resolve(strict=True))
    installer=ComponentInstaller(manager.config.root,manager.resources)
    python=installer.project.root / ".provider_envs/markitdown/Scripts/python.exe"
    uv=installer.uv
    assert hashlib.sha256(uv.read_bytes()).hexdigest()==UV_SHA256
    assert python.is_file()
    spec=(Path(__file__).resolve().parents[2]/"provider_specs/markitdown.txt").read_text(encoding="utf-8")
    assert "pydantic==2.10.6" in spec
    result={"private":str(manager.config.root),"pydantic_pin":"2.10.6"}
    try:
        installer.run([str(uv),"pip","install","--no-config","--python",str(python),
                       "--index-url","https://pypi.org/simple","pydantic==2.10.6"])
        check=subprocess.run([str(python),"-I","-c",
                              "from pydantic._internal._typing_extra import eval_type_backport; from mcp.server.fastmcp import FastMCP; print('LEGACY_MCP_IMPORT_OK')"],
                             capture_output=True,text=True,timeout=45)
        result["module_check"]={"exit_code":check.returncode,"stdout":check.stdout[-1500:],"stderr":check.stderr[-3000:]}
        assert check.returncode==0
        manager.components.stage(18931)
        manifest = manager.components.manifest_dir / "markitdown.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["runtime"]["discovery_timeout_seconds"] = 90
        manifest.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with socket.socket() as so:
            so.bind(("127.0.0.1",0));port=so.getsockname()[1]
        manager.config.save({"runtime_port":port})
        manager.start()
        enabled=manager.dispatch("provider_action",{"action":"enable","provider_id":"markitdown","confirmed":True})
        state=enabled["data"]["provider"]
        result["provider"]={"state":state["state"],"tool_count":state["tool_count"],"pid":manager.children["runtime"].pid}
        assert state["state"]=="ready" and state["tool_count"]>0
        manager.dispatch("provider_action",{"action":"disable","provider_id":"markitdown","confirmed":True})
        result["status"]="PASS"
    except Exception as e:
        result["status"]="FAIL";result["error"]=repr(e)
        raise
    finally:
        manager.stop()
        x.report.parent.mkdir(parents=True,exist_ok=True)
        x.report.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
        print(json.dumps({"status":result["status"],"report":str(x.report)}),flush=True)

if __name__=="__main__":main()
