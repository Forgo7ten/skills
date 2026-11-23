#!/usr/bin/env node
/** Zero-dependency TS runner: uses installed tsx when available, or Node >=22.6 native strip-types. */
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const skillDir = dirname(dirname(fileURLToPath(import.meta.url)));
const [major, minor] = process.versions.node.split(".").map(Number);

function runtimeFlags() {
  if (process.env.TEACHING_NATIVE_TS !== "1") {
    try {
      const tsxPath = createRequire(import.meta.url).resolve("tsx");
      return ["--import", pathToFileURL(tsxPath).href];
    } catch {
      // Fall back to Node native TypeScript execution when available.
    }
  }
  if (major > 22 || (major === 22 && minor >= 6)) {
    return ["--experimental-strip-types"];
  }
  console.error(`当前 Node ${process.version} 不支持原生 TypeScript 执行且未安装 tsx；请升级到 Node 22.6+ 或在 ${skillDir} 执行 npm install。`);
  process.exit(1);
}

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error("用法: node scripts/run.mjs <script.ts> [args...]");
  process.exit(1);
}
const [first, ...rest] = args;
const target = first.endsWith(".ts") && !first.includes("/") && !first.includes("\\")
  ? resolve(skillDir, "scripts", first)
  : first;
const result = spawnSync(process.execPath, [...runtimeFlags(), target, ...rest], {
  stdio: "inherit",
  windowsHide: true,
  env: { ...process.env, NODE_NO_WARNINGS: process.env.NODE_NO_WARNINGS ?? "1" },
});
if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
