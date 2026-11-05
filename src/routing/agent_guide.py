"""Live, read-only guidance for selecting existing PLA capabilities.

CHAGENT.md is advisory data, NOT trusted instructions or an authorization
source. The MCP tool contract remains unchanged: we only add a field to the
existing capability_search result and never execute a recommended provider.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

_PROVIDER = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_RULE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_BLOCK = re.compile(r"```pla-routing\s*\n(.*?)\n```", re.DOTALL)
_MAX_BYTES = 24_576
_MAX_ROUTES = 24
_MAX_KEYWORDS = 40
_MAX_PROVIDERS = 8


def _candidate_paths(
    data_root: Path | None = None, default_path: Path | None = None,
) -> tuple[Path, ...]:
    root = data_root
    if root is None and os.environ.get("PLA_DATA_ROOT"):
        root = Path(os.environ["PLA_DATA_ROOT"])
    if root is not None:
        override = root / "config" / "CHAGENT.md"
        if override.is_file() or override.is_symlink():
            return (override,)
    if default_path is not None:
        return (default_path,)
    packaged = os.environ.get("PLA_INSTALL_RESOURCES")
    if packaged and (Path(packaged) / "CHAGENT.md").is_file():
        return (Path(packaged) / "CHAGENT.md",)
    return (Path(__file__).resolve().parents[2] / "CHAGENT.md",)


def validate_guidance_text(contents: str) -> tuple[list[dict[str, Any]], str]:
    """Parse bounded editable guidance without executing arbitrary Markdown."""
    if not isinstance(contents, str):
        raise ValueError("invalid_guidance_content")
    if len(contents.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("guidance_too_large")
    match = _BLOCK.search(contents)
    if match is None:
        raise ValueError("routing_block_missing")
    config = json.loads(match.group(1))
    if not isinstance(config, dict) or set(config) != {"schema_version", "routes"}:
        raise ValueError("invalid_routing_schema")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("unsupported_routing_version")
    raw = config["routes"]
    if not isinstance(raw, list) or len(raw) > _MAX_ROUTES:
        raise ValueError("invalid_route_count")
    routes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"id", "keywords", "providers"}:
            raise ValueError("invalid_route")
        route_id = item["id"]
        keywords, providers = item["keywords"], item["providers"]
        if (
            not isinstance(route_id, str)
            or not _RULE_ID.fullmatch(route_id)
            or route_id in seen
        ):
            raise ValueError("invalid_route_id")
        if (
            not isinstance(keywords, list) or not 1 <= len(keywords) <= _MAX_KEYWORDS
            or any(not isinstance(k, str) or not 2 <= len(k) <= 64
                   or k != k.strip() for k in keywords)
        ):
            raise ValueError("invalid_keywords")
        if (
            not isinstance(providers, list) or not 1 <= len(providers) <= _MAX_PROVIDERS
            or any(not isinstance(p, str) or not _PROVIDER.fullmatch(p) for p in providers)
            or len(set(providers)) != len(providers)
        ):
            raise ValueError("invalid_providers")
        seen.add(route_id)
        routes.append({"id": route_id, "keywords": keywords, "providers": providers})
    return routes, hashlib.sha256(contents.encode("utf-8")).hexdigest()[:12]


def _read_rules(path: Path) -> tuple[list[dict[str, Any]], str]:
    if path.is_symlink():
        raise ValueError("symlink_not_allowed")
    if not path.is_file():
        raise ValueError("guidance_missing")
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError("guidance_too_large")
    return validate_guidance_text(path.read_text(encoding="utf-8-sig"))


def _top_capabilities(provider_id: str, snapshot: dict, query: str) -> list[dict]:
    available = [
        item for item in snapshot.get("capabilities", [])
        if item.get("provider_id") == provider_id and item.get("available") is True
    ]
    if not available:
        return []
    q = query.casefold()
    # When searching "制作PPT" we should show PPT-facing capabilities, even
    # if the generic registry search has no exact all-token match.
    domains = (
        ("ppt", "ppt", "presentation", "slide", "演示", "幻灯片"),
        ("excel", "excel", "spreadsheet", "workbook", "sheet", "表格"),
        ("word", "word", "document", "文档"),
        ("pdf", "pdf"),
    )
    terms: tuple[str, ...] = ()
    if any(term in q for term in ("ppt", "powerpoint", "幻灯片", "演示文稿", "母版", "元素复用", "图元复用", "技术框图")):
        terms = domains[0][1:]
    elif any(term in q for term in ("excel", "xlsx", "表格")):
        terms = domains[1][1:]
    elif any(term in q for term in ("word", "docx", "文档")):
        terms = domains[2][1:]
    elif "pdf" in q:
        terms = domains[3][1:]
    if not terms:
        return []
    scored = []
    for item in available:
        heading = " ".join((str(item.get("id", "")), str(item.get("title", "")))).casefold()
        score = sum(4 if term in heading else 0 for term in terms)
        if score:
            scored.append((-score, item["id"], item))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [
        {"id": x["id"], "title": x["title"]}
        for _, _, x in scored[:5]
    ]


def live_route_advice(
    query: str,
    registry: Any,
    *,
    data_root: Path | None = None,
    default_path: Path | None = None,
) -> dict[str, Any] | None:
    """Resolve editable task hints against real live registry availability.

    Called for every capability_search; it never caches the Markdown file.
    Invalid overrides fail closed to a visible diagnostic, not stale guidance.
    """
    try:
        rules, version = _read_rules(_candidate_paths(data_root, default_path)[0])
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "guidance_unreadable"
        # Never return raw exceptions or path contents.
        allowed = {
            "symlink_not_allowed", "guidance_missing", "guidance_too_large",
            "routing_block_missing", "invalid_routing_schema",
            "unsupported_routing_version", "invalid_route_count",
            "invalid_route", "invalid_route_id", "invalid_keywords",
            "invalid_providers",
        }
        return {"status": "config_error", "code": reason if reason in allowed else "invalid_json_or_encoding"}
    if not isinstance(query, str) or not query.strip():
        return None
    normalized = query.casefold()
    matched = next(
        ((rule, keyword) for rule in rules for keyword in rule["keywords"]
         if keyword.casefold() in normalized), None
    )
    if matched is None:
        return None
    rule, keyword = matched
    snapshot = registry.snapshot(include_unavailable=True)
    states = registry.provider_status()
    providers = []
    for provider_id in rule["providers"]:
        capabilities = _top_capabilities(provider_id, snapshot, query)
        available = bool(states.get(provider_id, False) and any(
            item.get("provider_id") == provider_id and item.get("available") is True
            for item in snapshot.get("capabilities", [])
        ))
        providers.append({
            "provider_id": provider_id,
            "available": available,
            "candidate_capabilities": capabilities if available else [],
        })
    preferred = next((p["provider_id"] for p in providers if p["available"]), None)
    return {
        "status": "advisory",
        "source": "CHAGENT.md",
        "revision": version,
        "rule_id": rule["id"],
        "preferred_provider": preferred,
        "providers": providers,
        "guidance": (
            "If applicable, describe a suggested capability and invoke it through "
            "the existing governed capability interface. Follow the user's explicit "
            "tool choice. Fall back to generic tools when specialized capabilities "
            "do not meet the task; never bypass existing permissions or confirmations."
        ),
    }
