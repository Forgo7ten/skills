#!/usr/bin/env tsx
import { fileURLToPath } from "node:url";
/** Create a narrowly scoped Agent Project checkpoint with the required source prefix. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  arg,
  argsAll,
  assertGitIdentity,
  git,
  gitRoot,
  gitStatus,
  gitText,
  parseCli,
  parseNul,
  safeRelativePath,
} from "./common.ts";

const TYPES = new Set(["initial", "feat", "fix", "refactor", "docs", "test", "build", "chore", "perf", "style", "ci", "revert"]);

function readPaths(args: ReturnType<typeof parseCli>): string[] {
  const values = argsAll(args, "path");
  const file = arg(args, "paths-file");
  if (file) {
    const parsed = JSON.parse(readFileSync(file, "utf8")) as unknown;
    const fromFile = Array.isArray(parsed) ? parsed : (parsed as { files?: unknown })?.files;
    if (!Array.isArray(fromFile) || !fromFile.every((item) => typeof item === "string")) {
      throw new Error(`paths-file 必须是字符串数组或包含 files 数组的 JSON：${file}`);
    }
    values.push(...(fromFile as string[]));
  }
  return [...new Set(values.map((item) => safeRelativePath(item)))].sort();
}

function main(): void {
  const args = parseCli(process.argv.slice(2));
  if (args.positionals.includes("--help") || args.values.has("help")) {
    console.log(`checkpoint.ts

用法:
  npx tsx checkpoint.ts --agent-dir <path> --source agent|user --type <type> --scope <scope> --message <description> --paths-file <paths.json>

Commit 格式：
  [agent] feat(scope): description
  [user] feat(scope): description

只暂存明确指定的文件。要求 Agent Project 暂存区在执行前干净；不修改 User Project。`);
    return;
  }

  const agentDir = arg(args, "agent-dir");
  const source = arg(args, "source");
  const type = arg(args, "type");
  const scope = arg(args, "scope");
  const description = arg(args, "message");
  if (!agentDir || !source || !type || !scope || !description) {
    throw new Error("需要 --agent-dir、--source、--type、--scope 和 --message");
  }
  if (source !== "agent" && source !== "user") throw new Error("--source 只能是 agent 或 user");
  if (!TYPES.has(type)) throw new Error(`不支持的 Commit type：${type}`);
  if (!/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(scope)) throw new Error(`非法 scope：${scope}`);
  if (description.trim().length === 0 || description.includes("\n")) throw new Error("Commit description 必须是非空单行文本");

  const root = gitRoot(agentDir);
  if (!root || resolve(root) !== resolve(agentDir)) throw new Error("Agent Project 必须是独立 Git 仓库");
  if (gitStatus(root, ["diff", "--cached", "--quiet"]) !== 0) {
    throw new Error("Agent Project 暂存区不干净。请先检查已有 staged changes，不要将无关改动混入本次 Commit。");
  }
  const paths = readPaths(args);
  if (paths.length === 0) throw new Error("必须通过 --paths-file 或 --path 指定本次 Commit 的文件");
  const coreDocs = ["docs/development-plan.md", "docs/development-log.md", "docs/teaching-state.md"];
  const changedDocs = new Set(parseNul(git(root, ["status", "--porcelain", "-z", "--no-renames", "--", ...coreDocs])).map((item) => item.slice(3)));
  const missingDocs = [...changedDocs].filter((doc) => !paths.includes(doc));
  if (missingDocs.length > 0) {
    throw new Error(`本步骤修改了教学文档，必须与本次代码原子提交；请把以下文件加入 --paths-file：\n${missingDocs.join("\n")}`);
  }
  const pathspecs = paths.map((path) => `:(literal)${path}`);
  git(root, ["add", "-A", "--", ...pathspecs]);
  const staged = parseNul(git(root, ["diff", "--cached", "--name-only", "-z"])).sort();
  const allowed = new Set(paths);
  const unexpected = staged.filter((path) => !allowed.has(path));
  if (unexpected.length > 0) throw new Error(`暂存结果包含未授权文件，停止提交：\n${unexpected.join("\n")}`);
  if (staged.length === 0) throw new Error("指定文件没有产生任何 Git 变更，未创建空 Commit。");

  assertGitIdentity(root);
  const commitMessage = `[${source}] ${type}(${scope}): ${description.trim()}`;
  git(root, ["commit", "-m", commitMessage]);
  console.log(JSON.stringify({
    status: "COMMITTED",
    message: commitMessage,
    commit: gitText(root, ["rev-parse", "HEAD"]).trim(),
    shortCommit: gitText(root, ["rev-parse", "--short", "HEAD"]).trim(),
    files: staged,
  }, null, 2));
}

const direct = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (direct) main();
