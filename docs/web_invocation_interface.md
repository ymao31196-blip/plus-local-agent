# PLA Web Invocation Interface

This is a **generic local web-to-PLA adapter**. PLA does not define course
components or web workflows: the page supplies each tool request and parameters,
and can sequence follow-up requests from earlier results. The adapter uses the
existing `TaskStore -> execute_local_tool` boundary, so PLA root policies,
process allow-lists, execution logs and batch restrictions still apply.

## Start (explicit opt-in, not automatically enabled)

Use an existing PLA Python environment with the repository dependencies (for
example the project's configured Conda environment). The web interface is
registered on the **same loopback HTTP server** as MCP, not a second process
competing to own the persistent task database.

PowerShell example, from the repository root:

```powershell
$env:PLA_WEB_API_TOKEN = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$env:PLA_WEB_ALLOWED_TOOLS = "list_directory,read_text,search_text,run_process"
$env:PLA_WEB_ALLOWED_ROOTS = "workspace"
$env:PLA_WEB_ALLOWED_ORIGINS = "http://127.0.0.1:3000"
Write-Host "Web API access token (keep private): $env:PLA_WEB_API_TOKEN"
python src/server.py --http --host 127.0.0.1 --port 8766 --path /mcp --web-api
```

Do not run a second server against an already-owned task database. For the
desktop workflow, use the Settings panel below: it configures the Desktop-owned
Runtime and avoids a second TaskStore. The interface is part of the PLA Desktop
v1.3.0 release candidate; older v1.2.0 installers do not contain it.

## Desktop-managed activation and connection diagnostics

The current Desktop source has an opt-in Web API panel under **Settings**.
This panel configures the *existing Desktop-owned Runtime*; it does not launch
a second server or a duplicate TaskStore. To activate it:

1. Stop Desktop Runtime and Tunnel before changing the Web API settings.
2. Enable the Web API. Enter the exact localhost Origin hosting the *trusted*
   page (e.g. `http://127.0.0.1:8896`), the authorized workspace Root(s),
   and permitted tools. Enable `run_process` explicitly for computations.
3. Save and start Runtime. Desktop generates a fresh ephemeral token for this
   Runtime session; it is not written to `desktop.json` or normal logs.
4. Click **检测本地连接**. The Manager performs an authenticated loopback
   GET `/web/v1/tools` with the allowed Origin and checks the observed tools
   and Roots against the saved settings. The response contains status/counts
   but *never* the token.
5. Click **显示本次访问Token** and copy it into the trusted page's *runtime
   token prompt*. Never embed credentials in webpage source. The API endpoint
   is the configured Desktop Runtime port, normally
   `http://127.0.0.1:18766/web/v1`.

Runtime stop/restart revokes the previous token. Currently there is **no
independent hot stop/start** of Web API while keeping the same Runtime: changing
the setting requires a Runtime restart. The v1.2.0 installer does not contain these changes. The v1.3.0 release
candidate must pass the frozen Runtime acceptance in the build script before
publication; this check exercises the packaged API rather than source code alone.

Verified on 2026-11-16: source Desktop Manager launched a separately isolated
Runtime on a fresh loopback port with a separate workspace/database and token;
`web_api_test` returned `ready`; authenticated HTTP submitted a parametrized
wind-power simulation through the real `TaskStore -> execute_local_tool ->
run_process` boundary and returned both JSON and PNG. The isolated Manager
then stopped and terminated only its own child processes. The reusable
demonstration and acceptance scripts live outside the repository in the
local Desktop folder `PLA-Web-Lab`. These are *integration evidence*, not
a claim that the new installer or arbitrary user-authored pages have passed
a production security audit.

The default web tool allow-list is only `list_directory,read_text,search_text`.
`run_process` must be explicitly enabled for numerical computation. The web
API has **no effect** unless `--web-api` is provided. A random 32+-character
bearer token is mandatory; it is never placed in the URL.

Serve your **trusted** generated web page on the explicitly configured origin
(e.g., `http://127.0.0.1:3000`). A `file://` document with Origin `null`
will be rejected. Do not commit tokens to the page's JavaScript, HTML, a Git
repository, or screenshots. For an exploratory page, prompt for the token at
runtime and keep it in volatile memory. Only expose allowed origins you trust.

## REST API (local HTTP only)

All endpoints are under `http://127.0.0.1:8766/web/v1` with
`Authorization: Bearer <token>`. JSON POSTs also require
`Content-Type: application/json`.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/tools` | Configured allowed tool schemas and roots |
| POST | `/tasks` | Submit exactly one tool request OR a fixed action array |
| GET | `/tasks/{task_id}` | Status, results, errors; optional `?cursor=N` for incremental events |
| POST | `/tasks/{task_id}/cancel` | Ask PLA's existing TaskStore to cancel a running/queued task |
| GET | `/artifact?root=workspace&path=plots%2Ffigure.png` | Read a small authorized PNG/JPEG/WEBP/GIF (max 12 MiB) |

Task payload example:

```json
{
  "tool": "run_process",
  "arguments": {
    "program": "python",
    "args": ["-c", "print(1 + 1)"],
    "root": "workspace",
    "cwd": ".",
    "timeout": 30
  }
}
```

Fixed batch example (up to 20 actions and **only** tools in PLA's existing
`ACTION_LOCAL_TOOLS` batch allow-list):

```json
{
  "actions": [
    {"tool": "list_directory", "arguments": {"path": ".", "root": "workspace"}},
    {"tool": "read_text", "arguments": {"path": "notes.txt", "root": "workspace"}}
  ],
  "stop_on_error": true
}
```

The current batch format does **not** interpolate results from one action into
the next. For a workflow with dynamic dependencies, the web page awaits one
task result, extracts an output path/parameter, then submits the next task.

In a browser, an image response requires a `fetch` with the bearer header,
then `URL.createObjectURL(await response.blob())` for an `<img>` element.
JSON and text results are returned as task records, including PLA's existing
truncation indicators and structured error information.

Minimal browser JavaScript (page served from an allow-listed origin):

```js
const token = window.prompt("PLA local access token:");
const base = "http://127.0.0.1:8766/web/v1";
const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

async function invoke(tool, arguments_) {
  const started = await fetch(`${base}/tasks`, {
    method: "POST", headers,
    body: JSON.stringify({ tool, arguments: arguments_ })
  });
  if (!started.ok) throw new Error(await started.text());
  const { task_id } = await started.json();
  while (true) {
    const reply = await fetch(`${base}/tasks/${task_id}`, { headers });
    if (!reply.ok) throw new Error(await reply.text());
    const task = await reply.json();
    if (["completed", "failed", "cancelled"].includes(task.status)) return task;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
}
```

Pass user-modified arguments to `invoke` to make a parametrized calculation
UI. For plots, prefer returning a compact numeric series in JSON where possible;
use the restricted `/artifact` endpoint for images created in an authorized
root. Nothing here requires generating or installing a PLA learning component.

## Security and limits

- Bound to `127.0.0.1` only; mismatched Host headers rejected.
- All read/write calls require a bearer token, and browser Origins must match
  the explicit allow-list. Preflight uses exact allowed methods and headers.
- Web tools and roots are independently allow-listed by the operator. The page
  cannot enlarge either list. Underlying PLA policies still apply.
- Direct POST bodies limited to 1 MiB; fixed batches to 20 actions; image
  output to 12 MiB with no SVG or arbitrary file download route.
- This is **not** an internet-facing API and has no remote-user authentication,
  multi-user isolation, approval UI or secure workflow sandbox. Do not expose
  it through a public reverse proxy or tunnel.
- Pages are executable code. Only serve pages you trust. For higher-risk tools,
  use separate short-lived sessions with narrowly scoped permissions.
- Static pages cannot directly summon the existing ChatGPT conversation to
  generate new reasoning turns; that is a separate integration problem.

## Verification

`tests/test_web_api.py` covers HTTP authentication, origins, Host validation,
preflight, tool and root restrictions, fixed batch policy, task parameter
forwarding, polling, image response, and token validation. Run it under the
project's Python test environment, e.g.:

```powershell
python -m pytest -q tests/test_web_api.py
```

The v1.3.0 candidate bundles this interface in the frozen Runtime; it remains
opt-in and disabled by default. It adds no MCP tool definitions. Do not treat
the feature as available in an installed binary until the v1.3.0 packaged
acceptance report passes.
