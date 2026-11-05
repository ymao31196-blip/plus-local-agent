/**
 * QMD pilot control MCP: fixed authorized test corpus only.
 * No arbitrary filesystem paths, shell commands, model downloads or user scripts.
 * This experiment is NOT a general-purpose workspace-index provider.
 */
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { readFileSync, realpathSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import path from "node:path";

const dataRoot = process.env.PLA_DATA_ROOT || String.raw`C:\Users\26286\AppData\Local\io.pla.desktop`;
const pilot = String.raw`C:\Users\26286\Desktop\PLA-QMD-pilot-20261028`;
const pilotConfig = path.join(pilot, ".qmd", "index.yml");
const approvedConfigHashes = new Set([
  "c77af58073b04fa8de203e19aae4e4200adfa97a92936a5754dab1b94799bd90",
  "445bbc8838322c7136d414f1a1ac0fe3dab693567a41a89391843c422e76b295",
]);
const envDir = path.join(dataRoot, "components", ".provider_envs", "qmd-pilot");
const qmdCli = path.join(envDir, "node_modules", "@tobilu", "qmd", "dist", "cli", "qmd.js");
const pkgPath = path.join(envDir, "node_modules", "@tobilu", "qmd", "package.json");
const require = createRequire(pkgPath);

function launchFailure(error) {
  console.error("QMD pilot startup failure:", String(error?.name || "Error"));
}
const [{ McpServer }, { serveStdio }, { z }] = await Promise.all([
  Promise.resolve().then(() => import(pathToFileURL(require.resolve("@modelcontextprotocol/server")).href)),
  Promise.resolve().then(() => import(pathToFileURL(require.resolve("@modelcontextprotocol/server/stdio")).href)),
  Promise.resolve().then(() => import(pathToFileURL(require.resolve("zod")).href)),
]).catch(error => { launchFailure(error); throw error; });

function validatedPilot() {
  if (!existsSync(qmdCli)) throw new Error("QMD package is not installed");
  const real = realpathSync.native(pilot);
  if (path.normalize(real).toLowerCase() !== path.normalize(pilot).toLowerCase()) {
    throw new Error("Pilot workspace was relocated or linked");
  }
  const content = readFileSync(pilotConfig);
  if (content.byteLength > 65536 ||
      !approvedConfigHashes.has(createHash("sha256").update(content).digest("hex"))) {
    throw new Error("Pilot QMD config changed: refuse unreviewed hooks/paths");
  }
}

async function runAction(action, query) {
  validatedPilot();
  if (!["update", "status", "search"].includes(action)) throw new Error("Unsupported action");
  if (action === "search" && (typeof query !== "string" || !query.trim() || query.length > 120)) {
    throw new Error("Bounded search query required");
  }
  const args = [qmdCli, action];
  if (action === "search") args.push(query, "--json", "-n", "10");

  // Do not propagate Tunnel secrets or opt-in trust settings.
  const env = {};
  for (const key of ["SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "HOME",
                      "USERPROFILE", "APPDATA", "LOCALAPPDATA"]) {
    if (process.env[key]) env[key] = process.env[key];
  }
  return await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, args, {
      cwd: pilot, env, shell: false, windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const pieces = [[], []]; let total = 0; let killed = false;
    const timer = setTimeout(() => {
      killed = true;
      child.kill();
    }, 70000);
    [child.stdout, child.stderr].forEach((stream, i) => stream.on("data", part => {
      if (total + part.length > 24000) {
        killed = true;
        child.kill();
        return;
      }
      total += part.length;
      pieces[i].push(part);
    }));
    child.on("error", reject);
    child.on("close", code => {
      clearTimeout(timer);
      const stdout = Buffer.concat(pieces[0]).toString("utf8");
      const stderr = Buffer.concat(pieces[1]).toString("utf8");
      resolve({ action, ok: !killed && code === 0, exit_code: code,
        stdout, stderr, bounded: true });
    });
  });
}

function buildServer() {
  const server = new McpServer({ name: "pla-qmd-pilot-control", version: "0.1.0" });
  server.registerTool("pilot_run", {
    title: "QMD Pilot Workspace Operation",
    description: "Restricted trial: update, status, or lexical search only inside PLA-QMD-pilot-20261028.",
    inputSchema: z.object({
      action: z.enum(["update", "status", "search"]),
      query: z.string().max(120).optional(),
    }),
    annotations: { readOnlyHint: false, openWorldHint: false },
  }, async ({ action, query }) => {
    try {
      const result = await runAction(action, query);
      return { content: [{ type: "text", text: JSON.stringify(result) }],
        structuredContent: result, isError: !result.ok };
    } catch (error) {
      return { content: [{ type: "text", text: "QMD pilot rejected: " + String(error?.message || error).slice(0,240) }],
        isError: true };
    }
  });
  return server;
}

try {
  const started = serveStdio(buildServer);
  Promise.resolve(started).catch(err => {
    launchFailure(err);
    process.exitCode = 1;
  });
} catch (err) {
  launchFailure(err);
  process.exitCode = 1;
}
