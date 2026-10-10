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
from desktop_runtime.components import ComponentProject
from desktop_runtime.capability_bridge import checked_provider_action, checked_skill_action
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
        self.components = ComponentProject(data, resources)
        self.children = {}
        self._installer_locks = {}
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

    def _spawn_installer(self, role, command):
        from desktop_runtime.component_lock import ComponentInstallLock
        ownership = ComponentInstallLock(self.config.root)
        ownership.acquire()
        try:
            process = self._spawn(role, command, self._environment())
        except BaseException:
            ownership.release()
            raise
        self._installer_locks[role] = ownership
        return process

    def _environment(self):
        # Never inherit developer provider/Tunnel overrides or unrelated credentials.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PLA_", "AGENT_", "CONTROL_PLANE_", "CLOUDFLARED_", "MCP_", "SKILL_LIBRARY_"))
               and k not in {"OPENAI_API_KEY", "PYTHONPATH", "PYTHONHOME"}}
        root = self.config.root
        self.components.stage(self.config.value["browser_port"])
        selected = set(self.components.preferences()["enabled"])
        if self.config.value["browser_enabled"]:
            selected.add("browser")
        env.update(PLA_DATA_ROOT=str(root), AGENT_PLA_ROOT=str(self.resources),
                   PLA_DESKTOP_RUNTIME="1",
                   PLA_RUNTIME_PORT=str(self.config.value["runtime_port"]), PLA_TUNNEL_HEALTH_PORT=str(self.config.value["health_port"]),
                   PLA_RESOURCE_ROOT=str(self.components.root), PLA_INSTALL_RESOURCES=str(self.resources),
                   PLA_BROWSER_PORT=str(self.config.value["browser_port"]),
                   AGENT_WORKSPACE=str(root / "workspace"),
                   AGENT_WORKSPACES_CONFIG=str(self.config.workspace_path),
                   PLA_DESKTOP_MANIFEST_DIR=str(self.components.manifest_dir),
                   PLA_EXTERNAL_PROVIDERS=",".join(sorted(selected)), PLA_EXTERNAL_OBSERVERS="")
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
        if getattr(sys, "frozen", False):
            for relative in ("runtime/pla-runtime.exe", "python/python.exe", "python/python311.dll"):
                if not (self.resources / relative).is_file():
                    raise RuntimeError(f"Required bundled component is missing: {relative}; repair the installation")
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
        for role in (*[name for name in self.children if name.startswith('installer:')], "tunnel", "runtime", "browser"):
            process = self.children.pop(role, None)
            if process is not None:
                terminate_owned_process_tree(process)
            reader = self.readers.pop(role, None)
            if reader is not None:
                reader.join(timeout=5)
        for ownership in self._installer_locks.values():
            ownership.release()
        self._installer_locks.clear()
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
        observed_providers = {}
        if ready:
            try:
                inventory = self._invoke_management_capability('runtime.provider_catalog', {})['data']['providers']
                observed_providers = {row['provider_id']: row.get('lifecycle') for row in inventory}
            except Exception as exc:
                self.last_error = redact(str(exc), self._secret)
        skill_state = observed_providers.get('skill-library')
        office_states = {name: observed_providers.get(name) for name in ('docx', 'pdf', 'office-enhancement', 'wps-office')}
        return {"config": self.config.public(), "runtime": "ready" if ready else "starting" if alive else "exited" if "runtime" in self.children else "stopped",
                "owned_processes": {role: {"pid": process.pid, "exit_code": process.poll()} for role, process in self.children.items()},
                "tunnel": tunnel, "chatgpt_authorization": "not_observable_locally",
                "tunnel_last_successful_poll": self.tunnel_readiness.last_success,
                "chatgpt_e2e": "not_verified", "local_mcp_verified": self.local_verified,
                "browser": browser, "office": office_states,
                "skills": skill_state or {'state': 'not_connected' if ready else 'not_observed'}, "last_error": self.last_error,
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

    async def _management_mcp(self, tool, arguments):
        from fastmcp import Client
        if not self._alive("runtime"):
            raise ValueError("Runtime is stopped")
        async with Client(f"http://127.0.0.1:{self.config.value['runtime_port']}/mcp", timeout=90) as client:
            result = await client.call_tool(tool, arguments)
            if result.is_error:
                raise RuntimeError("Runtime rejected the management operation")
            data = result.data
            if isinstance(data, dict) and (data.get('status') in {'failed', 'blocked', 'precondition_failed'} or data.get('is_error') is True):
                raise RuntimeError(redact(json.dumps(data, ensure_ascii=False), self._secret))
            return data

    def _invoke_management_capability(self, capability_id, arguments, confirmed=False):
        return asyncio.run(self._management_mcp('capability_invoke', {
            'capability_id': capability_id, 'arguments': arguments,
            'confirmation': 'INVOKE' if confirmed else None}))

    async def _probe_browser_component(self):
        from fastmcp import Client
        async with Client(f"http://localhost:{self.config.value['browser_port']}/mcp", timeout=3) as client:
            tools = await client.list_tools()
            if 'browser_snapshot' not in {tool.name for tool in tools}:
                raise RuntimeError('Unexpected Browser MCP component')

    def _save_provider_selection(self, provider_id, enabled):
        self.components.select(provider_id, enabled)
        if provider_id == 'browser':
            self.config.save({'browser_enabled': enabled})

    def dispatch(self, command, args):
        if not isinstance(args, dict):
            raise ValueError("Arguments must be an object")
        if command == "status":
            return self.status()
        if command == "provider_catalog":
            if args:
                raise ValueError("Provider catalog takes no arguments")
            catalog = self.components.stage(self.config.value["browser_port"])
            catalog['runtime_observed'] = False
            if self._alive('runtime'):
                result = self._invoke_management_capability('runtime.provider_catalog', {})
                actual = {row['provider_id']: row for row in result['data']['providers']}
                catalog['providers'] = [{**row, **actual.get(row['provider_id'], {})} for row in catalog['providers']]
                catalog['runtime_observed'] = True
            for row in catalog['providers']:
                if row['provider_id'] == 'browser':
                    row['requested_enabled'] = self.config.value['browser_enabled']
                provider_id = row['provider_id']
                specs = self.resources / 'provider-assets/provider_specs'
                from desktop_runtime.custom_packages import get_spec_paths
                spec_paths = get_spec_paths(self.components, provider_id)
                row['install_supported'] = provider_id == 'browser' or bool(spec_paths)
                row['install_source'] = ('bundled' if spec_paths[0].is_relative_to(self.resources)
                                         else 'user_custom') if spec_paths else None
                cwd = row.get('cwd')
                row['directory_exists'] = not cwd or Path(cwd).is_dir()
                receipt = self.components.root / 'receipts' / f'{provider_id}.json'
                row['installation_recorded'] = receipt.is_file()
                row['execution_files_present'] = (
                    row.get('python_exists') is not False
                    and row.get('command_exists') is not False
                    and row['directory_exists']
                )
                if provider_id == 'wps-office':
                    # A Node binary and an existing source directory alone do
                    # not constitute an installed WPS MCP. Half-completed npm
                    # setup otherwise looks enableable but instantly disconnects.
                    source_dir = Path(cwd)
                    entry = source_dir / 'dist/index.js'
                    dependencies = source_dir / 'node_modules'
                    row['wps_entrypoint_exists'] = entry.is_file()
                    row['wps_dependencies_exist'] = dependencies.is_dir()
                    row['execution_files_present'] = (
                        row['execution_files_present']
                        and row['wps_entrypoint_exists']
                        and row['wps_dependencies_exist']
                    )
            return catalog
        if command == 'provider_package':
            from desktop_runtime.custom_packages import package_spec
            if set(args) != {'provider_id', 'package_kind', 'packages', 'confirmed', 'expected_sha256'} or type(args['confirmed']) is not bool:
                raise ValueError('Preview the exact custom package pins before confirmation')
            if args['confirmed'] and self._alive('runtime'):
                actual = self._invoke_management_capability('runtime.provider_catalog', {})['data']['providers']
                if any(row['provider_id'] == args['provider_id'] and row.get('lifecycle', {}).get('enabled')
                       for row in actual if row.get('lifecycle')):
                    raise ValueError('Disable the custom provider before updating its packages')
            return package_spec(self.components, **args)
        if command == 'provider_import':
            if set(args) != {'content', 'confirmed', 'expected_sha256'} or type(args['confirmed']) is not bool:
                raise ValueError('Expected bounded manifest and review confirmation')
            self.components.stage(self.config.value['browser_port'])
            return self.components.import_manifest(args['content'], confirm=args['confirmed'], expected_sha256=args['expected_sha256'])
        if command == 'provider_configuration':
            if set(args) != {'provider_id', 'content', 'confirmed', 'expected_sha256', 'expected_content_sha256'}:
                raise ValueError('Expected provider configuration and exact review hashes')
            self.components.stage(self.config.value['browser_port'])
            return self.components.configure_manifest(args['provider_id'], args['content'], confirm=args['confirmed'],
                expected_sha256=args['expected_sha256'], expected_content_sha256=args['expected_content_sha256'])
        if command == 'provider_details':
            if set(args) != {'provider_id'} or args['provider_id'] not in {row['provider_id'] for row in self.components.catalog()['providers']}:
                raise ValueError('Select a known provider')
            details = asyncio.run(self._management_mcp('capability_catalog', args))
            details['diagnostic'] = asyncio.run(self._management_mcp('provider_doctor', {**args, 'live_probe': False, 'force': False}))
            return details
        if command == 'provider_action':
            capability_id, arguments = checked_provider_action(args)
            provider_id = args['provider_id']
            if args['action'] == 'enable' and provider_id != 'browser':
                catalog = self.components.catalog()['providers']
                row = next((item for item in catalog if item['provider_id'] == provider_id), None)
                if row is None:
                    raise ValueError('Unknown provider; refresh the catalog')
                if row.get('python_exists') is False or row.get('command_exists') is False:
                    raise ValueError(f'{provider_id} is not installed: executable missing; install the reviewed component first')
                if row.get('cwd') and not Path(row['cwd']).is_dir():
                    raise ValueError(f'{provider_id} is not installed: working directory missing; install the reviewed component first')
                if provider_id == 'wps-office':
                    source_dir = Path(row['cwd'])
                    if not (source_dir / 'dist/index.js').is_file() or not (source_dir / 'node_modules').is_dir():
                        raise ValueError('wps-office is not fully installed: missing dist/index.js or node_modules; repair the reviewed dependencies first')
            if not self._alive('runtime') and args['action'] in {'enable', 'disable'}:
                self._save_provider_selection(provider_id, args['action'] == 'enable')
                return {'status': 'selection_saved', 'provider_id': provider_id, 'connected': False, 'applies_on_runtime_start': True}
            if provider_id == 'browser' and args['action'] == 'enable' and not self._alive('browser'):
                self._free_port(self.config.value['browser_port'])
                self._spawn('browser', self._command('browser'), self._environment())
                deadline = time.monotonic() + 60
                while self._alive('browser') and time.monotonic() < deadline:
                    try:
                        asyncio.run(self._probe_browser_component())
                        break
                    except Exception:
                        time.sleep(0.3)
                else:
                    process = self.children.pop('browser', None)
                    if process is not None:
                        terminate_owned_process_tree(process)
                    raise RuntimeError('Browser component failed to become MCP-ready; see logs')
            result = self._invoke_management_capability(capability_id, arguments, True)
            if args['action'] in {'enable', 'disable'}:
                enabled = args['action'] == 'enable'
                self._save_provider_selection(provider_id, enabled)
                if provider_id == 'browser':
                    if not enabled:
                        process = self.children.pop('browser', None)
                        if process is not None:
                            terminate_owned_process_tree(process)
            return result
        if command == 'skill_action':
            capability_id, arguments = checked_skill_action(args)
            return self._invoke_management_capability(capability_id, arguments, args['confirmed'])
        if command == 'provider_bundle':
            from desktop_runtime.installer import ComponentInstaller, core_bundle_plan, CORE_BUNDLE_IDS
            import hashlib
            if set(args) != {'confirmed', 'expected_sha256'} or type(args['confirmed']) is not bool:
                raise ValueError('Review and confirm the exact starter bundle')
            plan = core_bundle_plan(ComponentInstaller(self.config.root, self.resources))
            digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            if not args['confirmed']:
                return {**plan, 'sha256': digest, 'started': False}
            if digest != args['expected_sha256']:
                raise ValueError('Starter bundle changed; preview its exact dependency plans again')
            if self._alive('runtime'):
                actual = self._invoke_management_capability('runtime.provider_catalog', {})['data']['providers']
                if any(row['provider_id'] in CORE_BUNDLE_IDS and row.get('lifecycle', {}).get('enabled')
                       for row in actual if row.get('lifecycle')):
                    raise ValueError('Disable starter-pack providers before updating their environments')
            if any(self._alive(name) for name in self.children if name.startswith('installer:')):
                raise ValueError('A component installation is already running')
            role = 'installer:starter-pack'
            command_line = self._command('component_install') + [
                '--data-dir', str(self.config.root), '--resources', str(self.resources),
                '--bundle', '--expected-plan-sha256', digest]
            self._spawn_installer(role, command_line)
            return {**plan, 'sha256': digest, 'started': True, 'job': role}
        if command == 'provider_install':
            from desktop_runtime.installer import ComponentInstaller
            import hashlib
            if set(args) != {'provider_id', 'skill_package', 'confirmed', 'expected_sha256'} or type(args['confirmed']) is not bool:
                raise ValueError('Expected provider installation preview and confirmation')
            plan = ComponentInstaller(self.config.root, self.resources).plan(args['provider_id'], args['skill_package'])
            digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            if not args['confirmed']:
                return {**plan, 'sha256': digest, 'started': False}
            if digest != args['expected_sha256']:
                raise ValueError('Review the exact dependency installation plan')
            if self._alive('runtime'):
                actual = self._invoke_management_capability('runtime.provider_catalog', {})['data']['providers']
                if any(row['provider_id'] == args['provider_id'] and row.get('lifecycle', {}).get('enabled') for row in actual if row.get('lifecycle')):
                    raise ValueError('Disable this provider before updating its environment')
            role = 'installer:' + args['provider_id']
            if any(self._alive(name) for name in self.children if name.startswith('installer:')):
                raise ValueError('A component installation is already running')
            command_line = self._command('component_install') + ['--data-dir', str(self.config.root), '--resources', str(self.resources), '--provider-id', args['provider_id'], '--expected-plan-sha256', digest]
            if args['skill_package']:
                command_line += ['--skill-package', str(args['skill_package'])]
            self._spawn_installer(role, command_line)
            return {**plan, 'sha256': digest, 'started': True, 'job': role}
        if command == 'installation_status':
            if args:
                raise ValueError('Installation status takes no arguments')
            jobs = []
            for name, child in self.children.items():
                if not name.startswith('installer:'):
                    continue
                code = child.poll()
                if code is not None:
                    ownership = self._installer_locks.pop(name, None)
                    if ownership is not None:
                        ownership.release()
                provider_id = name.partition(':')[2]
                receipt = self.components.root / 'receipts' / f'{provider_id}.json'
                if provider_id == 'starter-pack':
                    from desktop_runtime.installer import CORE_BUNDLE_IDS
                    installed = code == 0 and all((self.components.root / 'receipts' / (item + '.json')).is_file()
                                                     for item in CORE_BUNDLE_IDS)
                else:
                    installed = code == 0 and (receipt.is_file() or provider_id == 'browser')
                with self.log_lock:
                    log_lines = [line for line in self.logs if line.startswith(f'[{name}]')][-30:]
                jobs.append({'job': name, 'provider_id': provider_id, 'pid': child.pid,
                             'exit_code': code, 'state': 'running' if code is None else 'installed' if installed else 'failed',
                             'logs': log_lines})
            receipts = self.components.root / 'receipts'
            records = []
            if receipts.is_dir():
                for path in sorted(receipts.glob('*.json')):
                    if path.stem.isascii() and path.stem.replace('-', '').replace('_', '').isalnum():
                        records.append(path.stem)
            return {'jobs': jobs, 'installed_receipts': records}
        if command == 'skill_permissions':
            path = self.components.root / 'config/skill-library.desktop.json'
            roots = workspace_registry_status(self.config.workspace_path, self.resources)['roots']
            roots['workspace'] = {'path': str(self.config.root / 'workspace'), 'read': True, 'write': True, 'execute': True}
            if args:
                if set(args) != {'local_roots', 'writable_roots', 'confirmed'} or args['confirmed'] is not True:
                    raise ValueError('Review Skill root permissions before saving')
                read, write = args['local_roots'], args['writable_roots']
                if not isinstance(read, list) or not isinstance(write, list) or any(not isinstance(item, str) for item in read + write):
                    raise ValueError('Skill root permissions must be root name lists')
                if any(name not in roots or roots[name]['read'] is not True for name in read) or any(name not in read or roots[name]['write'] is not True for name in write):
                    raise ValueError('Skill permissions cannot exceed current workspace permissions')
                if self._alive('runtime'):
                    actual = self._invoke_management_capability('runtime.provider_catalog', {})['data']['providers']
                    if any(row['provider_id'] == 'skill-library' and row.get('lifecycle', {}).get('enabled') for row in actual if row.get('lifecycle')):
                        raise ValueError('Disable Skill Library before changing its permissions')
                atomic_write(path, json.dumps({'local_roots': sorted(set(read)), 'writable_roots': sorted(set(write))}).encode())
            value = json.loads(path.read_text()) if path.exists() else {'local_roots': [], 'writable_roots': []}
            return {**value, 'available_roots': roots}
        if command == 'development_status':
            if args:
                raise ValueError('Development status takes no arguments')
            registry = workspace_registry_status(self.config.workspace_path, self.resources)
            root = registry['roots'].get('pla-development')
            import shutil
            return {'enabled': bool(root and all(root.get(field) is True for field in ('read', 'write', 'execute'))),
                    'root_name': 'pla-development', 'root': root, 'config_sha256': registry['config_sha256'],
                    'installed_resources_read_only': True,
                    'system_build_prerequisites': {name: bool(shutil.which(name)) for name in ('git.exe', 'cargo.exe', 'rustc.exe', 'npm.cmd')},
                    'source_snapshot_available': (self.resources / 'developer-source.zip').is_file(),
                    'candidate_switch': 'explicit_local_installer_after_review'}
        if command == 'development_prepare':
            from desktop_runtime.development import prepare_source
            if set(args) != {'path', 'confirmed', 'expected_sha256'}:
                raise ValueError('Expected source preparation preview and confirmation')
            return prepare_source(self.config.root, self.resources, **args)
        if command == 'development_configure':
            if set(args) != {'enabled', 'path', 'confirmed', 'expected_sha256'} or type(args['enabled']) is not bool or args['confirmed'] is not True:
                raise ValueError('Review the development workspace grant before saving')
            if self._alive('runtime'):
                raise ValueError('Stop Runtime before changing workspace permissions')
            if args['enabled']:
                raw = args['path']
                if not isinstance(raw, str) or not Path(raw).is_absolute() or raw.startswith(('\\\\', '//')):
                    raise ValueError('Select an absolute local PLA source directory')
                path = Path(raw).resolve()
                if not all((path / name).is_file() for name in ('src/server.py', 'desktop/packaging/build.ps1', 'desktop/src-tauri/tauri.conf.json')):
                    raise ValueError('Select a complete PLA source workspace')
                if path == self.config.root or path in self.config.root.parents or self.config.root in path.parents:
                    raise ValueError('Development workspace must not overlap private application data')
                return workspace_registry_upsert(self.config.workspace_path, self.resources,
                    name='pla-development', path=str(path), read=True, write=True, execute=True,
                    expected_sha256=args['expected_sha256'])
            return workspace_registry_remove(self.config.workspace_path, self.resources,
                name='pla-development', expected_sha256=args['expected_sha256'])
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
                fields = dict(args)
                mode = fields.pop("mode", "upsert")
                if mode not in {"create", "update", "upsert"}:
                    raise ValueError("Invalid workspace save mode")
                existing = workspace_registry_status(self.config.workspace_path, self.resources)["roots"]
                name = fields.get("name")
                if mode == "create" and name in existing:
                    raise ValueError("Root name already exists; choose a different name or edit the existing root")
                if mode == "update" and name not in existing:
                    raise ValueError("Root no longer exists; refresh the workspace list")
                target = Path(args.get("path", "")).resolve()
                if not target.is_dir():
                    raise ValueError("Select an existing directory")
                if target == self.config.root or target in self.config.root.parents or self.config.root in target.parents:
                    raise ValueError("Workspace must not overlap private application data")
                return workspace_registry_upsert(self.config.workspace_path, self.resources, **fields)
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
                if len(raw) > 512000:
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
