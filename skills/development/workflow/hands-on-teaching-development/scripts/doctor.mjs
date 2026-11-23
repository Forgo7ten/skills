#!/usr/bin/env node
/** Zero-dependency preflight: works before npm install and supports Node >=22.6 native TS. */
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";

const require = createRequire(import.meta.url);
const issues = [];
const [major, minor] = process.versions.node.split(".").map(Number);
const supportsNativeTs = major > 22 || (major === 22 && minor >= 6);
if (major < 20) issues.push(`Node.js 需要 >=20（当前 ${process.version}）`);

const git = spawnSync("git", ["--version"], { encoding: "utf8", windowsHide: true });
if (git.error || git.status !== 0) issues.push("未检测到可执行的 Git CLI");

let hasTsx = false;
try {
  require.resolve("tsx/package.json");
  hasTsx = true;
} catch {
  hasTsx = false;
}

if (!hasTsx && !supportsNativeTs) {
  issues.push("缺少依赖 tsx（当前 Node 版本不支持原生 --experimental-strip-types）");
}

if (issues.length > 0) {
  console.error("hands-on-teaching-development 环境自检失败：\n- " + issues.join("\n- "));
  console.error("请升级到 Node 22.6+，或进入 Skill 安装目录执行 npm install，然后重跑 npm run doctor。\n不会执行任何项目初始化操作。");
  process.exitCode = 1;
} else {
  const mode = hasTsx ? "tsx 就绪" : "Node 原生 TypeScript 就绪（无需 npm install）";
  console.log(`环境检查通过：Node ${process.version}、${git.stdout.trim()}、${mode}。`);
}
