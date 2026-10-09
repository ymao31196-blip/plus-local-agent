"""Private stdio management RPC. No Shell, tool invocation or public listener."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Lock, Thread
import time
import urllib.request

from desktop_runtime.config import DesktopConfig, atomic_write, redact
from desktop_runtime.health import TunnelReadiness
from host.workspace_manager import workspace_registry_status, workspace_registry_upsert, workspace_registry_remove
from runtime.runtime_context import terminate_owned_process_tree


class Manager:
    def __init__(self, data: Path, resources: Path):
        data = data.resolve()
        resources = resources.resolve()
        if data == resources or resources in data.parents or data in resources.parents:
            raise ValueError("Private data must not overlap application resources")
        self.config = DesktopConfig(data)
        self.resources = resources.resolve()
        self.children = {}
        self.logs = deque(maxlen=500)
        self.log_lock = Lock()
        self.local_verified = False
        self.last_error = ""
        self._secret = ""
        self.readers = {}
        self.tunnel_readiness = TunnelReadiness()

    def _record(self, text):
        clean = redact(text, self._secret)[:4000]
        with self.log_lock:
            self.logs.append(clean)
            path = self.config.root / "logs" / "desktop.log"
            if path.exists() and path.stat().st_size > 2_000_000:
                os.replace(path, path.with_suffix(".previous.log"))
            with path.open("a", encoding="utf-8") as stream:
                stream.write(clean + "\n")

    def _spawn(self, role, command, env):
        process = subprocess.Popen(command, cwd=self.config.root, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.children[role] = process
        def collect():
            for line in iter(process.stdout.readline, b""):
                self._record(f"[{role}] " + line.decode("utf-8", errors="replace").rstrip())
            process.stdout.close()
        reader = Thread(target=collect, daemon=True)
        self.readers[role] = reader
        reader.start()
        return process

    def _environment(self):
        # Never inherit developer provider/Tunnel overrides or unrelated credentials.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLA_", "AGENT_", "CONTROL_PLANE_", "CLOUDFLARED_", "MCP_"))
               and k not in {"OPENAI_API_KEY", "PYTHONPATH", "PYTHONHOME"}}
        root = self.config.root
        env.update(PLA_DATA_ROOT=str(root), AGENT_PLA_ROOT=str(self.resources),
                   PLA_DESKTOP_RUNTIME="1",
                   PLA_RUNTIME_PORT=str(self.config.value["runtime_port"]), PLA_TUNNEL_HEALTH_PORT=str(self.config.value["health_port"]),
                   PLA_RESOURCE_ROOT=str(self.resources), PLA_BROWSER_PORT=str(self.config.value["browser_port"]),
                   AGENT_WORKSPACE=str(root / "workspace"),
                   AGENT_WORKSPACES_CONFIG=str(self.config.workspace_path),
                   PLA_EXTERNAL_PROVIDERS="", PLA_EXTERNAL_OBSERVERS="")
        if self.config.value["browser_enabled"]:
            template = self.resources / "browser-playwright.json"
            manifest = json.loads(template.read_text(encoding="utf-8"))
            manifest["runtime"]["url"] = f"http://localhost:{self.config.value['browser_port']}/mcp"
            manifest_dir = root / "config" / "desktop-providers"
            atomic_write(manifest_dir / "browser.json", json.dumps(manifest).encode("utf-8"))
            env["PLA_DESKTOP_MANIFEST_DIR"] = str(manifest_dir)
            env["PLA_EXTERNAL_PROVIDERS"] = "browser"
        for key, filename in (("AGENT_TASK_DB", "tasks"), ("AGENT_TRANSACTION_DB", "transactions"),
                              ("PLA_EVENT_DB", "events"), ("PLA_GATE_DB", "gates"), ("PLA_HOOK_DB", "hooks")):
            env[key] = str(root / "state" / (filename + ".sqlite3"))
        if not getattr(sys, "frozen", False):
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        python_dir = self.resources / "python"
        if (python_dir / "python.exe").exists():
            env["PATH"] = str(python_dir) + os.pathsep + env.get("PATH", "")
            env["PLA_EXECUTION_PYTHON"] = str(python_dir / "python.exe")
        return env

    def _command(self, role):
        if getattr(sys, "frozen", False):
            return [sys.executable, role]
        return [sys.executable, str(Path(__file__).with_name("entry.py")), role]

    def _tunnel_environment(self):
        # The vendor honors many config/profile/log environment variables. Pass
        # only OS/network prerequisites, never a developer profile or raw-log flag.
        allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
                   "HOMEDRIVE", "HOMEPATH", "PATH", "PATHEXT", "COMSPEC", "USERNAME", "USERDOMAIN",
                   "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "NUMBER_OF_PROCESSORS",
                   "PROCESSOR_ARCHITECTURE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                   "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG", "LC_ALL"}
        return {key: value for key, value in os.environ.items() if key.upper() in allowed}

    def _alive(self, role):
        child = self.children.get(role)
        return child is not None and child.poll() is None

    @staticmethod
    def _free_port(port):
        with socket.socket() as connection:
            connection.bind(("127.0.0.1", port))

    async def _probe(self, verify=False):
        from fastmcp import Client
        port = self.config.value["runtime_port"]
        async with Client(f"http://127.0.0.1:{port}/mcp", timeout=4) as client:
            tools = await client.list_tools()
            if "diagnose_client" not in {tool.name for tool in tools}:
                raise RuntimeError("Unexpected MCP Runtime tool catalog")
            if verify:
                result = await client.call_tool("diagnose_client", {})
                if result.is_error:
                    raise RuntimeError("Runtime diagnostic tool failed")
                return result.data
        return True

    def start(self):
        if self._alive("runtime"):
            return self.status()
        port = self.config.value["runtime_port"]
        self._free_port(port)  # Refuse any existing listener; never reuse or kill it.
        if self.config.value["browser_enabled"] and not self._alive("browser"):
            self._free_port(self.config.value["browser_port"])
            self._spawn("browser", self._command("browser"), self._environment())
        process = self._spawn("runtime", self._command("runtime") + ["--http", "--host", "127.0.0.1", "--port", str(port)], self._environment())
        deadline = time.monotonic() + 35
        while process.poll() is None and time.monotonic() < deadline:
            try:
                asyncio.run(self._probe())
                self.last_error = ""
                return self.status()
            except Exception:
                time.sleep(0.3)
        self.stop()
        raise RuntimeError("Runtime failed to become MCP-ready; see logs")

    def start_tunnel(self):
        if self._alive("tunnel"):
            return self.status()
        if not self._alive("runtime"):
            raise ValueError("Start Runtime before connecting Tunnel")
        cfg = self.config.value
        if not cfg["tunnel_id"] or not self.config.secret_path.exists():
            raise ValueError("Configure this machine's Tunnel ID and runtime API key")
        tunnel = self.resources / "tunnel" / "tunnel-client.exe"
        if not tunnel.is_file():
            raise RuntimeError("Bundled Tunnel component is missing")
        self._free_port(cfg["health_port"])
        self._secret = self.config.secret()
        env = self._tunnel_environment()
        env["CONTROL_PLANE_API_KEY"] = self._secret
        self.tunnel_readiness = TunnelReadiness()
        self._spawn("tunnel", [str(tunnel), "run", "--control-plane.api-key", "env:CONTROL_PLANE_API_KEY",
                              "--control-plane.tunnel-id", cfg["tunnel_id"],
                              "--mcp.server-url", f"http://127.0.0.1:{cfg['runtime_port']}/mcp",
                              "--health.listen-addr", f"127.0.0.1:{cfg['health_port']}", "--log.format", "json"], env)
        return self.status()

    def stop(self):
        for role in ("tunnel", "runtime", "browser"):
            process = self.children.pop(role, None)
            if process is not None:
                terminate_owned_process_tree(process)
            reader = self.readers.pop(role, None)
            if reader is not None:
                reader.join(timeout=5)
        self.local_verified = False
        self._secret = ""
        return self.config.public()

    def status(self):
        alive = self._alive("runtime")
        if not alive:
            self.local_verified = False
            if "runtime" in self.children:
                self.last_error = f"Runtime exited with code {self.children['runtime'].returncode}; restart it to recover"
        ready = False
        if alive:
            try:
                ready = bool(asyncio.run(self._probe()))
            except Exception as exc:
                self.last_error = redact(str(exc), self._secret)
        tunnel = "stopped"
        if self._alive("tunnel"):
            tunnel = "starting_or_disconnected"
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(f"http://127.0.0.1:{self.config.value['health_port']}/readyz", timeout=2) as response:
                    local_ready = response.status == 200
                with opener.open(f"http://127.0.0.1:{self.config.value['health_port']}/metrics", timeout=2) as response:
                    metrics = response.read(2_000_000).decode("utf-8", errors="replace")
                tunnel = self.tunnel_readiness.observe(local_ready, metrics, time.time())
            except Exception:
                pass
        elif "tunnel" in self.children:
            tunnel = "exited"
        browser = "disabled" if (self.resources / "node.exe").exists() else "component_missing"
        if self.config.value["browser_enabled"]:
            browser = "starting_or_unavailable"
            if ready:
                try:
                    browser = asyncio.run(self._browser_probe())
                except Exception:
                    browser = "unavailable"
        return {"config": self.config.public(), "runtime": "ready" if ready else "starting" if alive else "exited" if "runtime" in self.children else "stopped",
                "owned_processes": {role: {"pid": process.pid, "exit_code": process.poll()} for role, process in self.children.items()},
                "tunnel": tunnel, "chatgpt_authorization": "not_observable_locally",
                "tunnel_last_successful_poll": self.tunnel_readiness.last_success,
                "chatgpt_e2e": "not_verified", "local_mcp_verified": self.local_verified,
                "browser": browser, "office": "optional_component_not_installed",
                "skills": "optional_component_not_installed", "last_error": self.last_error,
                "workspaces": workspace_registry_status(self.config.workspace_path, self.resources)}

    async def _browser_probe(self):
        from fastmcp import Client
        async with Client(f"http://127.0.0.1:{self.config.value['runtime_port']}/mcp", timeout=5) as client:
            result = await client.call_tool("capability_describe", {"capability_id": "browser.inspect"}, raise_on_error=False)
            if result.is_error:
                return "unavailable"
            # Availability comes from actual provider discovery, not Node PID.
            available = result.data.get("available", False)
            if not available:
                return "unavailable"
        async with Client(f"http://localhost:{self.config.value['browser_port']}/mcp", timeout=5) as browser:
            tools = await browser.list_tools()
            if "browser_snapshot" not in {tool.name for tool in tools}:
                return "unavailable"
        edge_locations = [Path(os.environ.get(name, "C:/missing")) / "Microsoft/Edge/Application/msedge.exe"
                          for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        return "provider_ready" if any(path.is_file() for path in edge_locations) else "edge_missing"

    def dispatch(self, command, args):
        if not isinstance(args, dict):
            raise ValueError("Arguments must be an object")
        if command == "status":
            return self.status()
        if command == "detect_legacy":
            if set(args) != {"path"} or not isinstance(args["path"], str):
                raise ValueError("Provide the existing installation directory")
            raw = args["path"]
            path = Path(raw)
            if not path.is_absolute() or raw.startswith(("\\\\", "//")):
                raise ValueError("Select an absolute local installation directory")
            path = path.resolve()
            if not path.is_dir():
                raise ValueError("Installation directory does not exist")
            recognized = all((path / item).is_file() for item in ("src/server.py", "start_all.ps1", "stop_all.ps1"))
            return {"recognized_source_install": recognized,
                    "tunnel_config_present": recognized and (path / "config/tunnel.local.yaml").is_file(),
                    "workspace_config_present": recognized and (path / "config/workspaces.local.yaml").is_file(),
                    "imported": False, "processes_adopted": False}
        if command == "start":
            return self.start()
        if command == "connect":
            return self.start_tunnel()
        if command == "stop":
            return self.stop()
        if command == "restart":
            self.stop()
            return self.start()
        if command == "configure":
            if set(args) - {"autostart", "onboarding_complete"} and any(self._alive(role) for role in self.children):
                raise ValueError("Stop services before changing configuration")
            return self.config.save(args)
        if command == "credential":
            if set(args) != {"secret"}:
                raise ValueError("Expected secret field")
            if self._alive("tunnel"):
                raise ValueError("Stop Tunnel before replacing its credential")
            self.config.set_secret(args["secret"])
            return {"credential_saved": True}
        if command in {"workspace_save", "workspace_remove"}:
            if self._alive("runtime"):
                raise ValueError("Stop Runtime before changing workspace permissions")
            if command == "workspace_save":
                target = Path(args.get("path", "")).resolve()
                if not target.is_dir():
                    raise ValueError("Select an existing directory")
                if target == self.config.root or target in self.config.root.parents or self.config.root in target.parents:
                    raise ValueError("Workspace must not overlap private application data")
                return workspace_registry_upsert(self.config.workspace_path, self.resources, **args)
            return workspace_registry_remove(self.config.workspace_path, self.resources, **args)
        if command == "verify":
            if not self._alive("runtime"):
                raise ValueError("Runtime is stopped")
            result = asyncio.run(self._probe(verify=True))
            self.local_verified = True
            return {"local_mcp": "PASS", "diagnostic": result, "tunnel_e2e": "NOT TESTED", "chatgpt_e2e": "NOT TESTED"}
        if command == "diagnose":
            result = self.status()
            report = self.config.root / "logs" / "diagnostics.json"
            atomic_write(report, json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8"))
            return {**result, "report_path": str(report)}
        if command == "logs":
            with self.log_lock:
                return {"lines": list(self.logs)}
        raise ValueError("Unsupported management command")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    manager = Manager(args.data_dir, args.resources)
    try:
        for raw in sys.stdin.buffer:
            try:
                if len(raw) > 65536:
                    raise ValueError("Management request exceeds limit")
                request = json.loads(raw)
                if not isinstance(request, dict) or set(request) != {"command", "args"}:
                    raise ValueError("Invalid management envelope")
                result = {"ok": True, "result": manager.dispatch(request["command"], request["args"])}
            except Exception as exc:
                error = redact(str(exc), manager._secret)
                manager.last_error = error
                result = {"ok": False, "error": error, "error_type": type(exc).__name__}
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        manager.stop()
