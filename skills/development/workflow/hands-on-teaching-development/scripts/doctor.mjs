#!/usr/bin/env node
/** Zero-dependency preflight: works before npm install. */
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";

const require = createRequire(import.meta.url);
const issues = [];
const major = Number(process.versions.node.split(".")[0]);
if (major < 20) issues.push(`Node.js 需要 >=20（当前 ${process.version}）`);
const git = spawnSync("git", ["--version"], { encoding: "utf8", windowsHide: true });
if (git.error || git.status !== 0) issues.push("未检测到可执行的 Git CLI");
for (const dep of ["tsx/package.json", "typescript/package.json", "@types/node/package.json"]) {
  try {
    require.resolve(dep);
  } catch {
    issues.push(`缺少依赖 ${dep.replace(/\/package\.json$/, "")}`);
  }
}
if (issues.length > 0) {
  console.error("hands-on-teaching-development 环境自检失败：\n- " + issues.join("\n- "));
  console.error("请进入 Skill 安装目录执行 npm install，然后运行 npm run doctor。\n不会执行任何项目初始化操作。");
  process.exitCode = 1;
} else {
  console.log(`环境检查通过：Node ${process.version}、${git.stdout.trim()}、TypeScript 依赖就绪。`);
}
