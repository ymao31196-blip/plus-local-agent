"""Desktop configuration and per-user Windows DPAPI credential protection."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import tempfile

VERSION = "1.0.0-rc.2"


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pla-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


def protect(data: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is required for credentials")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = Blob()
    api = ctypes.WinDLL("crypt32", use_last_error=True)
    call = api.CryptUnprotectData if decrypt else api.CryptProtectData
    call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    call.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; protection is bound to the current Windows user.
    if not call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(result.data)


def redact(value: str, secret: str = "") -> str:
    if secret:
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
    value = re.sub(r"(?i)(authorization\s*[:=]\s*|bearer\s+)[^\s\"']+", r"\1[REDACTED]", value)
    return value


class DesktopConfig:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "config" / "desktop.json"
        self.secret_path = self.root / "config" / "tunnel.secret"
        self.workspace_path = self.root / "config" / "workspaces.local.yaml"
        for directory in ("config", "state", "logs", "workspace", "cache"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.value = {"schema_version": 1, "runtime_port": 18766, "health_port": 18082, "browser_port": 18931, "browser_enabled": False,
                      "tunnel_id": "", "onboarding_complete": False, "autostart": False}
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                raise ValueError("Unsupported desktop configuration; existing file preserved")
            if set(payload) - set(self.value):
                raise ValueError("Unsupported desktop configuration fields")
            self.value.update(payload)
        self.validate(self.value)

    @staticmethod
    def validate(value):
        for name in ("runtime_port", "health_port", "browser_port"):
            port = value[name]
            if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
                raise ValueError("Ports must be integers between 1024 and 65535")
        if len({value["runtime_port"], value["health_port"], value["browser_port"]}) != 3:
            raise ValueError("Runtime, browser and Tunnel require different ports")
        tunnel = value["tunnel_id"]
        if not isinstance(tunnel, str) or (tunnel and not re.fullmatch(r"tunnel_[a-z0-9]{32}", tunnel)):
            raise ValueError("Invalid Tunnel ID")
        for field in ("autostart", "onboarding_complete", "browser_enabled"):
            if not isinstance(value[field], bool):
                raise ValueError(f"{field} must be boolean")

    def save(self, changes):
        allowed = {"runtime_port", "health_port", "browser_port", "browser_enabled", "tunnel_id", "onboarding_complete", "autostart"}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise ValueError("Unsupported configuration fields")
        updated = {**self.value, **changes}
        self.validate(updated)
        atomic_write(self.path, json.dumps(updated, indent=2).encode("utf-8"))
        self.value = updated
        return self.public()

    def set_secret(self, secret):
        if not isinstance(secret, str) or not 10 <= len(secret) <= 4096 or any(c.isspace() for c in secret):
            raise ValueError("Provide a runtime API key without spaces")
        atomic_write(self.secret_path, protect(secret.encode("utf-8")))

    def secret(self):
        return protect(self.secret_path.read_bytes(), decrypt=True).decode("utf-8")

    def public(self):
        return {**self.value, "credential_saved": self.secret_path.exists(), "version": VERSION,
                "data_directory": str(self.root), "automatic_updates": "disabled_unsigned"}
