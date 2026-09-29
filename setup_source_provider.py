from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from source_provider_setup import setup_git_npm_source


_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install one reviewed source-backed PLA Provider."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    provider = str(args.provider).strip().casefold()
    if not _PROVIDER_ID.fullmatch(provider):
        raise ValueError("provider must be a valid reviewed provider id")
    if not 30 <= args.timeout_seconds <= 1200:
        raise ValueError("timeout-seconds must be between 30 and 1200")

    spec_path = root / "provider_specs" / f"{provider}.source.json"
    if not spec_path.is_file():
        raise FileNotFoundError(
            f"Source provider spec was not found: {spec_path}"
        )

    result = setup_git_npm_source(
        root,
        provider,
        spec_path,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
