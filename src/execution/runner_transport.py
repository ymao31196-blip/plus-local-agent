"""Authenticated Windows named-pipe transport for PLA's Execution Runner.

The transport carries resolved one-shot requests, durable request identifiers,
result lookup, cancellation, health checks, and lifecycle messages.  Execution
policy remains independently enforced by both the Control Plane and Runner.
Legacy ``SPIKE_PROTOCOL_VERSION`` naming is retained as an internal compatibility
constant while the protocol itself is production-active.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

from multiprocessing.connection import Client


SPIKE_PROTOCOL_VERSION = "4"
MAX_MESSAGE_BYTES = 1_000_000


def make_pipe_address() -> str:
    if os.name != "nt":
        raise RuntimeError("The runner transport spike currently requires Windows")
    return rf"\\.\pipe\pla-runner-{uuid4().hex}"


def write_auth_file(path: Path) -> bytes:
    key = os.urandom(32)
    path.write_bytes(key)
    return key


def read_auth_file(path: Path) -> bytes:
    key = path.read_bytes()
    if len(key) < 16:
        raise ValueError("runner auth key is too short")
    return key


def _encode(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("runner message exceeds maximum size")
    return raw


def _decode(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("runner message exceeds maximum size")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runner message must be a JSON object")
    return payload


class RunnerSpikeClient:
    def __init__(self, address: str, authkey: bytes) -> None:
        self.address = address
        self.authkey = authkey

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        connection = Client(self.address, family="AF_PIPE", authkey=self.authkey)
        try:
            connection.send_bytes(_encode(payload))
            return _decode(connection.recv_bytes(MAX_MESSAGE_BYTES))
        finally:
            connection.close()

    def ping(self) -> dict[str, Any]:
        return self.request({"op": "ping", "protocol_version": SPIKE_PROTOCOL_VERSION})

    def probe(self) -> dict[str, Any]:
        return self.request(
            {"op": "probe", "protocol_version": SPIKE_PROTOCOL_VERSION}
        )

    def submit_resolved(
        self,
        *,
        execution_request_id: str,
        command: list[str],
        selected_root: str,
        cwd_relative: str,
        timeout: int,
        env_overrides: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> dict[str, Any]:
        return self.request(
            {
                "op": "submit_resolved",
                "protocol_version": SPIKE_PROTOCOL_VERSION,
                "execution_request_id": execution_request_id,
                "command": command,
                "selected_root": selected_root,
                "cwd_relative": cwd_relative,
                "timeout": timeout,
                "env_overrides": env_overrides or {},
                "stdin": stdin,
            }
        )

    def execution_result(self, execution_request_id: str) -> dict[str, Any]:
        return self.request(
            {
                "op": "execution_result",
                "protocol_version": SPIKE_PROTOCOL_VERSION,
                "execution_request_id": execution_request_id,
            }
        )

    def cancel_execution(self, execution_request_id: str) -> dict[str, Any]:
        return self.request(
            {
                "op": "cancel_execution",
                "protocol_version": SPIKE_PROTOCOL_VERSION,
                "execution_request_id": execution_request_id,
            }
        )

    def run_resolved(
        self,
        *,
        command: list[str],
        selected_root: str,
        cwd_relative: str,
        timeout: int,
        env_overrides: dict[str, str] | None = None,
        stdin: str | None = None,
        execution_request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = execution_request_id or uuid4().hex
        submitted = self.submit_resolved(
            execution_request_id=request_id,
            command=command,
            selected_root=selected_root,
            cwd_relative=cwd_relative,
            timeout=timeout,
            env_overrides=env_overrides,
            stdin=stdin,
        )
        if submitted.get("status") == "error":
            return submitted
        deadline = time.monotonic() + timeout + 10
        while time.monotonic() < deadline:
            result = self.execution_result(request_id)
            if result.get("status") != "running":
                return result
            time.sleep(0.05)
        raise TimeoutError("execution runner result polling exceeded bounded deadline")

    def shutdown(self) -> dict[str, Any]:
        return self.request({"op": "shutdown", "protocol_version": SPIKE_PROTOCOL_VERSION})


def start_detached_runner(
    *,
    address: str,
    auth_file: Path,
    state_file: Path,
) -> subprocess.Popen:
    """Start the spike runner as a detached Windows process.

    The returned handle is useful to a launcher, but the service is intentionally
    able to outlive that launcher.  Formal lifecycle ownership is a later phase.
    """

    if os.name != "nt":
        raise RuntimeError("The runner transport spike currently requires Windows")
    flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    service = Path(__file__).resolve().with_name("execution_runner_service.py")
    # This launcher also runs outside PowerShell (HTTP and standalone clients).
    # Give the detached child the same source package root explicitly.
    source_root = str(service.parent.parent)
    env = os.environ.copy()
    paths = [source_root] + [
        path for path in env.get("PYTHONPATH", "").split(os.pathsep)
        if path and path != source_root
    ]
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return subprocess.Popen(
        [
            sys.executable,
            str(service),
            "--address",
            address,
            "--auth-file",
            str(auth_file.resolve()),
            "--state-file",
            str(state_file.resolve()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
        cwd=str(service.parents[2]),
        env=env,
    )


def wait_for_runner_state(state_file: Path, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("status") == "ready":
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(0.05)
    raise TimeoutError("runner spike did not become ready")


def encode_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def decode_bytes(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)
