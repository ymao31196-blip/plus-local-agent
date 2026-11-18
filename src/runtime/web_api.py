"""Opt-in, loopback-only HTTP adapter for trusted human-authored web pages.

The web page owns its workflow. PLA only validates and dispatches calls to the
existing TaskStore -> execute_local_tool boundary. No web page can register tools
or override PLA's file/process security policies.
"""
from __future__ import annotations

import re
from hmac import compare_digest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tooling.internal_tool_executor import (
    ACTION_LOCAL_TOOLS,
    EXECUTABLE_LOCAL_TOOLS,
    INTERNAL_TOOL_SCHEMAS,
)
from tooling.local_tools import safe_path

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_TASK_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_JSON = 1_000_000
_MAX_IMAGE = 12 * 1024 * 1024
_MAX_BATCH = 20
_DEFAULT_TOOLS = frozenset({"list_directory", "read_text", "search_text"})
_ROOT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _valid_loopback_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and port is not None and 1024 <= port <= 65535
        and parsed.netloc == f"{parsed.hostname}:{port}"
        and not parsed.path and not parsed.query and not parsed.fragment
    )


def names_from_env(value: str | None, default: frozenset[str]) -> frozenset[str]:
    if value is None:
        return default
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def register_web_api(
    mcp: Any,
    task_store: Any,
    *,
    token: str,
    host_port: int,
    allowed_tools: frozenset[str] | None = None,
    allowed_origins: frozenset[str] | None = None,
    allowed_roots: frozenset[str] | None = None,
) -> None:
    """Register bounded REST routes on the existing FastMCP HTTP server.

    Enabled only from server.py --http --web-api. Credentials and permitted
    origins/tools are configured by the local operator, not by submitted pages.
    """
    if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
        raise ValueError("PLA_WEB_API_TOKEN must contain at least 32 non-whitespace characters")
    tools = _DEFAULT_TOOLS if allowed_tools is None else frozenset(allowed_tools)
    if not tools or not tools <= EXECUTABLE_LOCAL_TOOLS:
        raise ValueError("PLA_WEB_ALLOWED_TOOLS must be a nonempty subset of existing local tools")
    origins = (
        frozenset({f"http://127.0.0.1:{host_port}"})
        if allowed_origins is None else frozenset(allowed_origins)
    )
    if not origins or any(not _valid_loopback_origin(origin) for origin in origins):
        raise ValueError("PLA_WEB_ALLOWED_ORIGINS must contain explicit localhost/127.0.0.1 origins with a valid port")
    roots = frozenset({"workspace"}) if allowed_roots is None else frozenset(allowed_roots)
    if not roots or any(not isinstance(r, str) or not _ROOT_NAME.fullmatch(r) or r.casefold() == "pla" for r in roots):
        raise ValueError("PLA_WEB_ALLOWED_ROOTS must contain valid workspace root names")
    expected_host = f"127.0.0.1:{host_port}"

    def response(request: Request, payload: dict, status: int = 200) -> JSONResponse:
        result = JSONResponse(payload, status_code=status)
        secure_headers(request, result)
        return result

    def secure_headers(request: Request, result: Response) -> None:
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "no-referrer"
        origin = request.headers.get("origin")
        if origin in origins:
            result.headers["Access-Control-Allow-Origin"] = origin
            result.headers["Vary"] = "Origin"
            result.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            result.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            result.headers["Access-Control-Max-Age"] = "600"

    def verify(request: Request, *, preflight: bool = False) -> Response | None:
        # Reject DNS rebinding, accidental tunnel forwarding and disallowed origins.
        if request.headers.get("host") != expected_host:
            return response(request, {"error": "LocalHostRequired"}, 403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            return response(request, {"error": "OriginNotAllowed"}, 403)
        if preflight:
            if not origin or request.headers.get("access-control-request-method") not in {"GET", "POST"}:
                return response(request, {"error": "PreflightNotAllowed"}, 403)
            return response(request, {"ok": True}, 200)
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or not compare_digest(
            authorization[7:], token
        ):
            return response(request, {"error": "Unauthorized"}, 401)
        return None

    async def check_body(request: Request) -> tuple[dict | None, Response | None]:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return None, response(request, {"error": "JSONContentTypeRequired"}, 415)
        if request.headers.get("content-length", "").isdigit() and int(
            request.headers["content-length"]
        ) > _MAX_JSON:
            return None, response(request, {"error": "RequestTooLarge"}, 413)
        data = await request.body()
        if len(data) > _MAX_JSON:
            return None, response(request, {"error": "RequestTooLarge"}, 413)
        try:
            import json
            parsed = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return None, response(request, {"error": "InvalidJSON"}, 400)
        if not isinstance(parsed, dict):
            return None, response(request, {"error": "BodyMustBeObject"}, 400)
        return parsed, None

    def validate_action(action: Any, *, batch: bool = False) -> dict:
        if not isinstance(action, dict) or set(action) != {"tool", "arguments"}:
            raise ValueError("Each action requires only tool and arguments")
        tool, args = action["tool"], action["arguments"]
        if not isinstance(tool, str) or tool not in tools:
            raise ValueError("Tool is not enabled for the web interface")
        if batch and tool not in ACTION_LOCAL_TOOLS:
            raise ValueError("Tool cannot be used in a batch")
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        if "root" in args and args["root"] not in roots:
            raise ValueError("Root is not allowed for the web interface")
        if "destination_root" in args and args["destination_root"] is not None and args["destination_root"] not in roots:
            raise ValueError("Destination root is not allowed for the web interface")
        # A root omitted from the tool request defaults to workspace.
        if "root" not in args and "workspace" not in roots:
            raise ValueError("Explicit allowed root is required")
        return {"tool": tool, "arguments": args}

    @mcp.custom_route("/web/v1/tools", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def list_tools(request: Request) -> Response:
        problem = verify(request, preflight=request.method == "OPTIONS")
        if problem is not None:
            return problem
        return response(request, {
            "tools": [x for x in INTERNAL_TOOL_SCHEMAS if x["name"] in tools],
            "allowed_roots": sorted(roots),
            "max_batch": _MAX_BATCH,
        })

    @mcp.custom_route("/web/v1/tasks", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def create_task(request: Request) -> Response:
        problem = verify(request, preflight=request.method == "OPTIONS")
        if problem is not None:
            return problem
        body, problem = await check_body(request)
        if problem is not None:
            return problem
        try:
            if "actions" in body:
                if set(body) - {"actions", "stop_on_error"}:
                    raise ValueError("Unsupported task fields")
                actions = body["actions"]
                if not isinstance(actions, list) or not 1 <= len(actions) <= _MAX_BATCH:
                    raise ValueError("actions must contain 1-20 entries")
                stop = body.get("stop_on_error", True)
                if type(stop) is not bool:
                    raise ValueError("stop_on_error must be boolean")
                task = {
                    "actions": [validate_action(a, batch=True) for a in actions],
                    "stop_on_error": stop,
                }
            else:
                task = validate_action(body)
        except (ValueError, TypeError) as exc:
            return response(request, {"error": "InvalidTask", "message": str(exc)}, 400)
        # Add a provenance label without changing existing task execution semantics.
        task["web_origin"] = request.headers.get("origin", "local-client")
        result = task_store.submit(task)
        return response(request, result, 202 if "task_id" in result else 503)

    @mcp.custom_route("/web/v1/tasks/{task_id}", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def get_task(request: Request) -> Response:
        problem = verify(request, preflight=request.method == "OPTIONS")
        if problem is not None:
            return problem
        task_id = request.path_params["task_id"]
        if not _TASK_ID.fullmatch(task_id):
            return response(request, {"error": "InvalidTaskID"}, 400)
        raw = request.query_params.get("cursor")
        if raw is not None and (not raw.isdecimal() or len(raw) > 18):
            return response(request, {"error": "InvalidCursor"}, 400)
        result = task_store.get(task_id, int(raw) if raw is not None else None)
        error = result.get("error")
        missing = isinstance(error, dict) and error.get("type") == "TaskNotFound"
        return response(request, result, 404 if missing else 200)

    @mcp.custom_route("/web/v1/tasks/{task_id}/cancel", methods=["POST", "OPTIONS"], include_in_schema=False)
    async def cancel_task(request: Request) -> Response:
        problem = verify(request, preflight=request.method == "OPTIONS")
        if problem is not None:
            return problem
        task_id = request.path_params["task_id"]
        if not _TASK_ID.fullmatch(task_id):
            return response(request, {"error": "InvalidTaskID"}, 400)
        result = task_store.cancel(task_id)
        error = result.get("error")
        missing = isinstance(error, dict) and error.get("type") == "TaskNotFound"
        return response(request, result, 404 if missing else 200)

    @mcp.custom_route("/web/v1/artifact", methods=["GET", "OPTIONS"], include_in_schema=False)
    async def artifact(request: Request) -> Response:
        problem = verify(request, preflight=request.method == "OPTIONS")
        if problem is not None:
            return problem
        root = request.query_params.get("root", "workspace")
        path = request.query_params.get("path", "")
        if root not in roots or not path or len(path) > 2048:
            return response(request, {"error": "InvalidArtifactPath"}, 400)
        if Path(path).suffix.lower() not in _IMAGE_TYPES:
            return response(request, {"error": "ImageTypeNotAllowed"}, 415)
        try:
            file = safe_path(path, root, "read")
            if not file.is_file():
                return response(request, {"error": "ArtifactNotFound"}, 404)
            if file.stat().st_size > _MAX_IMAGE:
                return response(request, {"error": "ArtifactTooLarge"}, 413)
            data = file.read_bytes()
            if len(data) > _MAX_IMAGE:
                return response(request, {"error": "ArtifactTooLarge"}, 413)
        except (ValueError, OSError, PermissionError):
            return response(request, {"error": "ArtifactNotAccessible"}, 403)
        result = Response(data, media_type=_IMAGE_TYPES[file.suffix.lower()])
        secure_headers(request, result)
        return result
