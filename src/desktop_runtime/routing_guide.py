"""Private Desktop-only CHAGENT.md viewing and editing, with CAS and validation."""
from __future__ import annotations
import hashlib
from pathlib import Path
import re
from desktop_runtime.config import atomic_write
from routing.agent_guide import _candidate_paths, validate_guidance_text

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BYTES = 24_576

def _paths(data_root: Path, install_resources: Path) -> tuple[Path, Path, Path]:
    override = data_root / "config" / "CHAGENT.md"
    default = install_resources / "CHAGENT.md"
    if not default.is_file():
        default = Path(__file__).resolve().parents[2] / "CHAGENT.md"
    current = _candidate_paths(data_root=data_root, default_path=default)[0]
    return current, override, default

def _read(path: Path) -> str:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Routing file or directory cannot be a symlink")
    if not path.is_file() or path.stat().st_size > _MAX_BYTES:
        raise ValueError("Routing file unavailable or too large")
    return path.read_text(encoding="utf-8-sig")

def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def read_guide(data_root: Path, install_resources: Path) -> dict:
    current, override, default = _paths(data_root, install_resources)
    content = _read(current)
    baseline = _read(default)
    try:
        routes, revision = validate_guidance_text(content)
        validity = {"status": "valid", "route_count": len(routes), "revision": revision}
    except (ValueError, UnicodeError) as exc:
        validity = {"status": "invalid", "reason": str(exc)[:90]}
    return {
        "content": content, "default_content": baseline,
        "source": "personal" if current == override else "default",
        "expected_sha256": _digest(content), "validation": validity,
        "path": str(override),
    }

def save_guide(data_root: Path, install_resources: Path, args: dict) -> dict:
    if set(args) != {"content", "expected_sha256"}:
        raise ValueError("Routing save requires content and expected_sha256")
    content = args["content"]
    digest = args["expected_sha256"]
    if not isinstance(content, str) or not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise ValueError("Invalid routing content or revision")
    if not content.strip():
        raise ValueError("Routing content cannot be empty")
    validate_guidance_text(content)
    current = read_guide(data_root, install_resources)
    if current["expected_sha256"] != digest:
        raise ValueError("Routing file changed since last read; reload before saving")
    target = data_root / "config" / "CHAGENT.md"
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("Routing target cannot be a symlink")
    atomic_write(target, content.encode("utf-8"))
    return read_guide(data_root, install_resources)
