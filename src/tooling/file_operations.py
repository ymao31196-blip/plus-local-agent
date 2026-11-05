"""Conservative, structured file and ZIP operations for named PLA roots.

All paths are resolved through the existing named-root policy. No arbitrary
PowerShell/script execution is involved. Batch sequencing is handled by the
existing execute_actions dispatcher (not by this module).
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_FILES = 4000
MAX_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 256 * 1024 * 1024
MAX_RATIO = 500
BLOCK_BYTES = 1024 * 1024


def _api():
    # Lazy import avoids a cycle with local_tools' exported function table.
    from tooling import local_tools
    return local_tools


def _resolve(path: str, root: str, access: str) -> Path:
    policy = _api().root_policy(root)
    checked = policy.resolve(root, path, access)
    # Resolve validates containment; separately refuse symbolic aliases even
    # when a link happens to point *inside* the authorized root.
    base = checked.root.path.resolve()
    supplied = Path(path)
    lexical = Path(os.path.abspath(supplied if supplied.is_absolute() else base / supplied))
    try:
        parts = lexical.relative_to(base).parts
    except ValueError:
        return checked.target  # Policy already rejected any out-of-root resolution.
    cursor = base
    for name in parts:
        cursor = cursor / name
        if _is_link(cursor):
            raise ValueError("Symlink or reparse point is not supported")
    return checked.target


def _rel(path: Path, root: str) -> str:
    return _api()._relative_path(path, root)


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, FileNotFoundError):
        return False


def _check_nonlink(path: Path) -> None:
    if path.is_symlink() or _is_link(path):
        raise ValueError("Symlink or reparse point is not supported")


def _collect(source: Path, root: str, *, access: str = "read") -> list[tuple[Path, str]]:
    """Inventory a source tree before any mutation; reject links/private entries."""
    if not source.exists():
        raise ValueError("Source does not exist")
    _check_nonlink(source)
    result: list[tuple[Path, str]] = []
    total = 0
    pending = [source]
    while pending:
        current = pending.pop()
        if len(result) >= MAX_FILES:
            raise ValueError("Too many files or directories")
        checked = _resolve(str(current), root, access)
        if checked != current:
            raise ValueError("Path changed during validation")
        _check_nonlink(current)
        if current.is_dir():
            result.append((current, "dir"))
            pending.extend(reversed(list(current.iterdir())))
        elif current.is_file():
            size = current.stat().st_size
            if size > MAX_ENTRY_BYTES or total + size > MAX_BYTES:
                raise ValueError("File operation exceeds size limit")
            total += size
            result.append((current, "file"))
        else:
            raise ValueError("Unsupported filesystem entry")
    return result


def _destination(path: str, root: str) -> Path:
    target = _resolve(path, root, "write")
    if target == _resolve(".", root, "write"):
        raise ValueError("Refusing to replace the root directory")
    if target.exists() or target.is_symlink():
        _check_nonlink(target)
    return target


def _parent(target: Path, root: str) -> None:
    parent = _resolve(str(target.parent), root, "write")
    if parent != target.parent:
        raise ValueError("Destination parent changed")
    if not parent.is_dir():
        raise ValueError("Destination parent does not exist")
    _check_nonlink(parent)


def _independent(source: Path, target: Path) -> None:
    if source == target or source in target.parents:
        raise ValueError("Destination overlaps source")


def _result(operation: str, root: str, *, source: Path | None = None,
            destination: Path | None = None, destination_root: str | None = None,
            count: int = 0, bytes_total: int = 0) -> dict:
    value = {"status": "completed", "operation": operation,
             "entries": count, "bytes": bytes_total}
    if source is not None:
        value["source"] = _rel(source, root)
        value["source_root"] = root
    if destination is not None:
        dst_root = destination_root or root
        value["destination"] = _api().root_policy(dst_root).resolve(dst_root, str(destination), "write").relative
        value["destination_root"] = dst_root
    return value


def _validate_operation(
    operation: str, source: str | None, destination: str | None,
    sources: list[str] | None, root: str, destination_root: str | None,
    overwrite: bool,
) -> str:
    if operation not in {"mkdir", "copy", "move", "rename", "archive_create", "archive_extract"}:
        raise ValueError("Unsupported file operation")
    if not isinstance(overwrite, bool):
        raise TypeError("overwrite must be a boolean")
    if not isinstance(root, str) or not root:
        raise ValueError("root must be non-empty")
    if destination_root is not None and (not isinstance(destination_root, str) or not destination_root):
        raise ValueError("destination_root must be non-empty")
    if not isinstance(destination, str) or not destination:
        raise ValueError("destination is required")
    if operation in {"copy", "move", "rename", "archive_extract"} and (
        not isinstance(source, str) or not source
    ):
        raise ValueError("source is required")
    if operation == "archive_create" and (
        not isinstance(sources, list) or not 1 <= len(sources) <= 128
        or any(not isinstance(p, str) or not p for p in sources)
    ):
        raise ValueError("sources must contain 1 to 128 paths")
    if operation != "archive_create" and sources is not None:
        raise ValueError("sources is only valid for archive_create")
    if operation in {"mkdir", "archive_create"} and source is not None:
        raise ValueError("source is not valid for this operation")
    if operation == "rename" and destination_root not in (None, root):
        raise ValueError("rename cannot cross roots")
    return destination_root or root


def file_operation(
    operation: str,
    destination: str,
    source: str | None = None,
    sources: list[str] | None = None,
    root: str = "workspace",
    destination_root: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Perform one bounded, authorized filesystem operation.

    A directory copy or move rejects symlinks, reparse points and private paths.
    Overwrite only applies to existing regular *files*, never directories.
    Extraction stages all content before publishing entries. Sequential merge
    into an existing directory may partially complete on filesystem failure.
    """
    dst_root = _validate_operation(
        operation, source, destination, sources, root, destination_root, overwrite,
    )
    target = _destination(destination, dst_root)
    if operation == "mkdir":
        # Validate every new ancestor; may not create an unauthorized private path.
        missing = []
        node = target
        while not node.exists():
            missing.append(node)
            node = node.parent
        if not node.is_dir():
            raise ValueError("An ancestor is not a directory")
        for part in reversed(missing):
            if _resolve(str(part), dst_root, "write") != part:
                raise ValueError("Directory changed during validation")
        target.mkdir(parents=True, exist_ok=True)
        return _result(operation, dst_root, destination=target)

    if operation == "archive_create":
        if target.suffix.lower() != ".zip":
            raise ValueError("Destination must be a .zip file")
        _parent(target, dst_root)
        if target.exists() and (not overwrite or not target.is_file()):
            raise FileExistsError("Destination already exists")
        inventories = []
        seen = set()
        total_files = total_bytes = 0
        for item in sources or []:
            original = _resolve(item, root, "read")
            entries = _collect(original, root)
            for path, kind in entries:
                name = (PurePosixPath(original.name) /
                        PurePosixPath(path.relative_to(original).as_posix())).as_posix()
                if name in {"", "."}:
                    raise ValueError("Archive source needs a basename")
                normalized = name.casefold()
                if normalized in seen:
                    raise ValueError("Duplicate archive entry")
                seen.add(normalized)
                if kind == "file":
                    total_bytes += path.stat().st_size
                    total_files += 1
                inventories.append((path, name, kind))
        if len(inventories) > MAX_FILES or total_bytes > MAX_BYTES:
            raise ValueError("Archive size limit exceeded")
        if any(path == target for path, _, _ in inventories):
            raise ValueError("Archive cannot include itself")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".pla-archive-",
                                             suffix=".zip", delete=False) as tmp:
                temporary = Path(tmp.name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path, name, kind in inventories:
                    _collect(path, root) if kind == "file" else None  # recheck links
                    if kind == "dir":
                        zf.writestr(name.rstrip("/") + "/", b"")
                    else:
                        zf.write(path, name)
            _destination(str(target), dst_root)
            if target.exists() and not overwrite:
                raise FileExistsError("Destination already exists")
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return _result(operation, dst_root, destination=target,
                       count=total_files, bytes_total=total_bytes)

    src = _resolve(source, root, "read")
    if operation == "archive_extract":
        if not src.is_file():
            raise ValueError("ZIP source must be a file")
        if src.stat().st_size > MAX_BYTES:
            raise ValueError("Compressed ZIP exceeds size limit")
        _check_nonlink(src)
        if target.exists() and not target.is_dir():
            raise ValueError("Extraction destination must be a directory")
        _parent(target, dst_root)
        seen = set()
        with zipfile.ZipFile(src) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_FILES:
                raise ValueError("Archive entry count exceeds limit")
            bytes_total = 0
            entries = []
            for info in infos:
                name = info.filename
                normalized = name.replace("\\", "/")
                parts = normalized.rstrip("/").split("/")
                if (not normalized or normalized.startswith("/")
                    or any(
                        part in {"", ".", ".."} or ":" in part
                        or part.endswith((" ", "."))
                        or any(ord(char) < 32 for char in part)
                        or part.split(".")[0].upper() in {
                            "CON", "PRN", "AUX", "NUL",
                            *(f"COM{i}" for i in range(1, 10)),
                            *(f"LPT{i}" for i in range(1, 10)),
                        }
                        for part in parts
                    )):
                    raise ValueError("Unsafe ZIP path")
                if (info.external_attr >> 16) and stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("ZIP symlink is not supported")
                key = normalized.rstrip("/").casefold()
                if key in seen:
                    raise ValueError("Duplicate ZIP entry")
                seen.add(key)
                if not info.is_dir():
                    bytes_total += info.file_size
                    if (info.file_size > MAX_ENTRY_BYTES or bytes_total > MAX_BYTES
                        or info.file_size > max(info.compress_size, 1) * MAX_RATIO):
                        raise ValueError("Archive exceeds extraction limits")
                final = _destination(str(target.joinpath(*parts)), dst_root)
                if final != target and target in final.parents:
                    entries.append((info, final, parts))
                else:
                    raise ValueError("ZIP entry escapes destination")
                if final.exists():
                    if info.is_dir() and not final.is_dir():
                        raise FileExistsError("ZIP directory conflicts with file")
                    if not info.is_dir() and (final.is_dir() or not overwrite):
                        raise FileExistsError("ZIP file destination exists")
            with tempfile.TemporaryDirectory(dir=target.parent, prefix=".pla-extract-") as tmp:
                staging = Path(tmp)
                for info, _, parts in entries:
                    staged = staging.joinpath(*parts)
                    if info.is_dir():
                        staged.mkdir(parents=True, exist_ok=True)
                        continue
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    remaining = info.file_size
                    with zf.open(info) as inp, staged.open("wb") as out:
                        while True:
                            chunk = inp.read(BLOCK_BYTES)
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise ValueError("ZIP entry larger than declared")
                            out.write(chunk)
                    if remaining:
                        raise ValueError("ZIP entry shorter than declared")
                # New directory publication is a single same-volume rename.
                # An existing directory still needs sequential merge semantics.
                if not target.exists():
                    _destination(str(target), dst_root)
                    os.rename(staging, target)
                    return _result(operation, root, source=src, destination=target, destination_root=dst_root,
                                   count=len(infos), bytes_total=bytes_total)
                target.mkdir(parents=True, exist_ok=True)
                for info, _, parts in entries:
                    final = _destination(str(target.joinpath(*parts)), dst_root)
                    if info.is_dir():
                        if final.exists() and not final.is_dir():
                            raise FileExistsError("ZIP directory conflicts with file")
                        final.mkdir(parents=True, exist_ok=True)
                    else:
                        final.parent.mkdir(parents=True, exist_ok=True)
                        if final.exists() and not overwrite:
                            raise FileExistsError("ZIP destination exists")
                        os.replace(staging.joinpath(*parts), final)
        return _result(operation, root, source=src, destination=target, destination_root=dst_root,
                       count=len(infos), bytes_total=bytes_total)

    # copy / move / rename
    if operation in {"move", "rename"}:
        _resolve(source, root, "write")
    inventory = _collect(src, root, access="write" if operation in {"move", "rename"} else "read")
    if operation == "rename" and src.parent != target.parent:
        raise ValueError("rename must stay inside its source directory")
    _independent(src, target)
    _parent(target, dst_root)
    if target.exists():
        if not overwrite or not (src.is_file() and target.is_file()):
            raise FileExistsError("Destination already exists")
    count = sum(1 for _, kind in inventory if kind == "file")
    total = sum(path.stat().st_size for path, kind in inventory if kind == "file")
    if operation == "copy":
        if src.is_dir():
            with tempfile.TemporaryDirectory(dir=target.parent, prefix=".pla-copy-") as tmp:
                staged = Path(tmp) / "payload"
                shutil.copytree(src, staged, symlinks=True)
                # A changed source symlink is copied as a link, not followed.
                # Never publish it if the post-copy inventory rejects it.
                _collect(staged, dst_root, access="write")
                if target.exists():
                    raise FileExistsError("Destination already exists")
                staged.rename(target)
        else:
            # Copy to a temporary sibling first to avoid a partial final file.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".pla-copy-",
                                                 delete=False) as tmp:
                    temporary = Path(tmp.name)
                shutil.copy2(src, temporary)
                if target.exists() and not overwrite:
                    raise FileExistsError("Destination already exists")
                os.replace(temporary, target)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    elif operation == "rename":
        if target.exists() and overwrite:
            os.replace(src, target)
        else:
            src.rename(target)
    else:  # move; cross-volume moves may not be atomic
        if target.exists() and overwrite:
            os.replace(src, target) if src.is_file() else None
        else:
            shutil.move(str(src), str(target))
    return _result(operation, root, source=src, destination=target,
                   destination_root=dst_root, count=count, bytes_total=total)
