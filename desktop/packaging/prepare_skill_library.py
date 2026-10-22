"""Prepare the reviewed, deterministic Skill Library wheel for PLA Desktop.

This is a *build-time* developer step. End users never need this repository.
The private source is neither embedded as a checkout nor copied with user Skills.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import os
import shutil
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

SKILL_LIBRARY_COMMIT = "9c7f1844835c72a976950f5c6f088756e4875853"
SKILL_LIBRARY_WHEEL = "chatgpt_skill_library-0.5.0-py3-none-any.whl"
SKILL_LIBRARY_SHA256 = "5fee6f1cfd32c896b6d951e69b9631c167398d26e04bccfa9f18536d67611909"
SOURCE_DATE_EPOCH = "1791540741"


def check_source(source: Path) -> None:
    if not source.is_dir() or not (source / "pyproject.toml").is_file():
        raise ValueError(f"Missing reviewed Skill Library source checkout: {source}")
    git = lambda *args: subprocess.run(
        ["git", "-C", str(source), *args], capture_output=True, text=True,
        check=True, timeout=30, encoding="utf-8", errors="replace"
    ).stdout.strip()
    if git("rev-parse", "HEAD") != SKILL_LIBRARY_COMMIT:
        raise ValueError("Skill Library source revision differs from reviewed v0.5.0 commit")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Skill Library source checkout is dirty; refusing package substitution")


def prepare(source: Path, resources: Path) -> Path:
    source = source.resolve(strict=True)
    check_source(source)
    dest_dir = resources.resolve() / "provider-assets" / "packages"
    with TemporaryDirectory(prefix="pla-skill-wheel-") as temp:
        # Build a temporary tracked snapshot: setuptools must never write its
        # build/egg-info output into the independent source repository.
        tracked = subprocess.run(
            ["git", "-C", str(source), "ls-files", "-z"],
            capture_output=True, check=True, timeout=30,
        ).stdout.decode("utf-8").split("\0")
        build_source = Path(temp) / "source"
        for name in filter(None, tracked):
            relative = Path(name)
            original = source / relative
            if (relative.is_absolute() or ".." in relative.parts
                    or original.is_symlink()
                    or not original.resolve().is_relative_to(source)):
                raise ValueError("Unsafe reviewed source snapshot member")
            target = build_source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        env = os.environ.copy()
        env["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        command = [
            sys.executable, "-m", "pip", "wheel", "--no-deps",
            "--no-build-isolation", "--no-index", "--no-cache-dir",
            "--wheel-dir", temp, str(build_source),
        ]
        subprocess.run(command, env=env, check=True, timeout=120)
        wheel = Path(temp) / SKILL_LIBRARY_WHEEL
        if not wheel.is_file():
            raise ValueError("Expected v0.5.0 universal Wheel not produced")
        digest = sha256(wheel.read_bytes()).hexdigest()
        if digest != SKILL_LIBRARY_SHA256:
            raise ValueError(f"Skill Library Wheel hash mismatch: {digest}")
        with zipfile.ZipFile(wheel) as archive:
            if any(name.startswith("skills/") for name in archive.namelist()):
                raise ValueError("Unexpected private Skill content inside service package")
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Only one owned reviewed artifact; never copy arbitrary developer files.
        output = dest_dir / SKILL_LIBRARY_WHEEL
        staging = dest_dir / (SKILL_LIBRARY_WHEEL + ".tmp")
        try:
            staging.write_bytes(wheel.read_bytes())
            os.replace(staging, output)
        finally:
            staging.unlink(missing_ok=True)
    print(f"Reviewed bundled Skill Library Wheel: {output} SHA256={SKILL_LIBRARY_SHA256}")
    return output


def prepare_prebuilt(wheel: Path, resources: Path) -> Path:
    """Install a reviewed wheel when the separate source checkout is unavailable.

    The pinned digest is identical to the independently rebuilt source wheel.
    Untrusted or changed binary packages are never accepted as CI inputs.
    """
    wheel = wheel.resolve(strict=True)
    if wheel.name != SKILL_LIBRARY_WHEEL:
        raise ValueError("Expected the exact reviewed Skill Library wheel filename")
    content = wheel.read_bytes()
    digest = sha256(content).hexdigest()
    if digest != SKILL_LIBRARY_SHA256:
        raise ValueError(f"Reviewed Skill Library Wheel hash mismatch: {digest}")
    with zipfile.ZipFile(wheel) as archive:
        if any(name.startswith("skills/") for name in archive.namelist()):
            raise ValueError("Unexpected private Skill content inside service package")
    destination = resources.resolve() / "provider-assets" / "packages"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / SKILL_LIBRARY_WHEEL
    staging = destination / (SKILL_LIBRARY_WHEEL + ".tmp")
    try:
        staging.write_bytes(content)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
    print(f"Reviewed bundled Skill Library Wheel: {target} SHA256={digest}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    source_option = parser.add_mutually_exclusive_group(required=True)
    source_option.add_argument("--source", type=Path)
    source_option.add_argument("--wheel", type=Path)
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    if args.wheel is not None:
        prepare_prebuilt(args.wheel, args.resources)
    else:
        prepare(args.source, args.resources)


if __name__ == "__main__":
    main()
