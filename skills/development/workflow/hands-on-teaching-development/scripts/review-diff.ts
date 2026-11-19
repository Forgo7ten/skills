#!/usr/bin/env tsx
import { fileURLToPath } from "node:url";
/**
 * Compare the User Project worktree implementation with one Agent Project commit.
 * The User Project index is treated as the baseline; no files or index entries are changed.
 */
import { existsSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import {
  arg,
  copyEntryNoFollow,
  getGitTreeEntry,
  getIndexEntry,
  getWorktreeState,
  git,
  gitRoot,
  gitText,
  indexState,
  parseCli,
  parseNul,
  parentOrEmptyTree,
  readJson,
  safeRelativePath,
  pathIn,
  statesEqual,
  treeState,
  writeJson,
  type FileState,
} from "./common.ts";

interface ReviewSnapshot {
  version: 1;
  createdAt: string;
  status: "EXACT_MATCH" | "DIFFERENT" | "BASELINE_MISMATCH";
  userRepo: string;
  agentRepo: string;
  agentCommit: string;
  agentParent: string;
  paths: string[];
  extraUserPaths: string[];
  baselineStates: Record<string, FileState>;
  userStates: Record<string, FileState>;
  agentStates: Record<string, FileState>;
}

function resolveAgentCommit(agent: string, requested?: string): string {
  const commit = requested ?? gitText(agent, ["log", "-1", "--format=%H", "--grep=^\\[agent\\]"]).trim();
  if (!commit) throw new Error("找不到 Agent Project 最近的 [agent] Commit");
  const subject = gitText(agent, ["show", "-s", "--format=%s", commit]).trim();
  if (!subject.startsWith("[agent]")) throw new Error(`指定提交不是 [agent] Commit：${subject}`);
  return gitText(agent, ["rev-parse", `${commit}^{commit}`]).trim();
}

function readPathsFile(path: string): string[] {
  const parsed = JSON.parse(readFileSync(path, "utf8")) as unknown;
  const values = Array.isArray(parsed) ? parsed : (parsed as { files?: unknown })?.files;
  if (!Array.isArray(values) || !values.every((item) => typeof item === "string")) {
    throw new Error(`paths-file 必须是字符串数组，或包含 files 字符串数组的 JSON 对象：${path}`);
  }
  return [...new Set(values.map((item) => safeRelativePath(item as string)))].sort();
}

function defaultCommitPaths(agent: string, commit: string): string[] {
  const paths = parseNul(git(agent, ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", commit]));
  return paths.filter((path) => ![
    "docs/development-plan.md",
    "docs/development-log.md",
    "docs/teaching-state.md",
  ].includes(path));
}

/** Include untracked files in a portable, binary-capable Git patch.
 * `git diff` alone does not report them; `--no-index` returns exit code 1 on changes.
 * Use bare relative paths: Git apply rejects the a/./file form on some versions.
 */
function untrackedPatches(user: string, paths: string[]): Buffer {
  const patches: Buffer[] = [];
  for (const rel of paths) {
    const safe = safeRelativePath(rel);
    patches.push(git(user, [
      "diff", "--no-index", "--binary", "--no-ext-diff", "--no-color",
      "--", "/dev/null", safe,
    ], { allowCodes: [0, 1] }));
  }
  return Buffer.concat(patches);
}

function snapshotPath(workspace: string): string {
  return join(resolve(workspace), ".teaching", "review", "latest.json");
}

function review(args: ReturnType<typeof parseCli>): void {
  const userArg = arg(args, "user-dir");
  const agentArg = arg(args, "agent-dir");
  if (!userArg || !agentArg) throw new Error("review 需要 --user-dir 和 --agent-dir");
  const user = resolveRepo(userArg);
  const agent = resolveRepo(agentArg);
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const commit = resolveAgentCommit(agent, arg(args, "agent-commit"));
  const parent = parentOrEmptyTree(agent, commit);
  const pathsFile = arg(args, "paths-file");
  const paths = pathsFile ? readPathsFile(pathsFile) : defaultCommitPaths(agent, commit);
  if (paths.length === 0) throw new Error("当前 Agent Commit 没有可审查的代码文件；请提供当前步骤的 --paths-file");

  const baselineStates: Record<string, FileState> = {};
  const userStates: Record<string, FileState> = {};
  const agentStates: Record<string, FileState> = {};
  const baselineMismatch: string[] = [];
  const different: string[] = [];

  for (const rel of paths) {
    const baseTree = treeState(getGitTreeEntry(agent, parent, rel));
    const indexEntry = getIndexEntry(user, rel);
    if (indexEntry && indexEntry.stage !== "0") throw new Error(`User Project 暂存区存在冲突条目：${rel}`);
    const baseIndex = indexState(indexEntry);
    const current = getWorktreeState(user, rel);
    const expected = treeState(getGitTreeEntry(agent, commit, rel));
    baselineStates[rel] = baseTree;
    userStates[rel] = current;
    agentStates[rel] = expected;
    if (!statesEqual(baseTree, baseIndex)) baselineMismatch.push(rel);
    if (!statesEqual(current, expected)) different.push(rel);
  }

  const userTrackedChanges = parseNul(git(user, ["diff", "--name-only", "-z", "--no-ext-diff"]));
  const userUntrackedChanges = parseNul(git(user, ["ls-files", "--others", "--exclude-standard", "-z"]));
  const userChangedSet = new Set([...userTrackedChanges, ...userUntrackedChanges]);
  const extraUserPaths = [...userChangedSet].filter((rel) => !paths.includes(rel)).sort();

  let status: ReviewSnapshot["status"];
  if (baselineMismatch.length > 0) status = "BASELINE_MISMATCH";
  else if (different.length > 0 || extraUserPaths.length > 0) status = "DIFFERENT";
  else status = "EXACT_MATCH";

  const snapshot: ReviewSnapshot = {
    version: 1,
    createdAt: new Date().toISOString(),
    status,
    userRepo: user,
    agentRepo: agent,
    agentCommit: commit,
    agentParent: parent,
    paths,
    extraUserPaths,
    baselineStates,
    userStates,
    agentStates,
  };
  const manifestPath = snapshotPath(workspace);
  writeJson(manifestPath, snapshot);

  const reportDir = join(dirname(manifestPath), "latest");
  mkdirSync(reportDir, { recursive: true });
  const untrackedDir = join(reportDir, "user-untracked");
  const extraDir = join(reportDir, "user-extra");
  rmSync(untrackedDir, { recursive: true, force: true });
  rmSync(extraDir, { recursive: true, force: true });
  const gitUntracked = new Set(userUntrackedChanges);
  const untrackedPaths = paths.filter((rel) => gitUntracked.has(rel) && userStates[rel].exists);
  for (const rel of untrackedPaths) {
    copyEntryNoFollow(pathIn(user, rel), pathIn(untrackedDir, rel));
  }
  // `git ls-files --others` reports an embedded, untracked Git repository as
  // a directory path ending in `/` (for example `vendor/lib/`). It is not a
  // file patch and must not be recursively copied, including its nested .git.
  const patchableExtraUserPaths = extraUserPaths.filter((rel) => !rel.endsWith("/"));
  for (const rel of patchableExtraUserPaths) {
    if (gitUntracked.has(rel)) {
      try { copyEntryNoFollow(pathIn(user, rel), pathIn(extraDir, rel)); } catch { /* Deleted or unsupported extra path remains represented in the report. */ }
    }
  }
  const pathspecs = paths.map((path) => `:(literal)${path}`);
  const extraPathspecs = patchableExtraUserPaths.map((path) => `:(literal)${path}`);
  const agentPatch = git(agent, ["diff", "--binary", "--no-ext-diff", "--no-color", parent, commit, "--", ...pathspecs]);
  const userTrackedPatch = git(user, ["diff", "--binary", "--no-ext-diff", "--no-color", "--", ...pathspecs]);
  const userPatch = Buffer.concat([userTrackedPatch, untrackedPatches(user, untrackedPaths)]);
  const extraTrackedPatch = extraPathspecs.length > 0
    ? git(user, ["diff", "--binary", "--no-ext-diff", "--no-color", "--", ...extraPathspecs])
    : Buffer.alloc(0);
  const extraUserPatch = Buffer.concat([
    extraTrackedPatch,
    untrackedPatches(user, patchableExtraUserPaths.filter((rel) => gitUntracked.has(rel))),
  ]);
  writeFileSync(join(reportDir, "agent.patch"), agentPatch);
  writeFileSync(join(reportDir, "user.patch"), userPatch);
  writeFileSync(join(reportDir, "user-extra.patch"), extraUserPatch);
  writeFileSync(join(reportDir, "paths.json"), `${JSON.stringify(paths, null, 2)}\n`);
  writeFileSync(join(reportDir, "extra-user-paths.json"), `${JSON.stringify(extraUserPaths, null, 2)}\n`);

  const output = {
    status,
    agentCommit: commit,
    agentParent: parent,
    paths,
    baselineMismatch,
    different,
    extraUserPaths,
    unpatchableExtraDirectories: extraUserPaths.filter((rel) => rel.endsWith("/")),
    untrackedPaths,
    snapshot: manifestPath,
    reports: {
      agentPatch: join(reportDir, "agent.patch"),
      userPatch: join(reportDir, "user.patch"),
      userExtraPatch: join(reportDir, "user-extra.patch"),
      userUntrackedFiles: untrackedDir,
      extraUserUntrackedFiles: extraDir,
    },
  };
  console.log(JSON.stringify(output, null, 2));
}

function resolveRepo(path: string): string {
  const physical = realpathSync(path);
  const repo = gitRoot(physical);
  if (!repo) throw new Error(`不是 Git 仓库：${path}`);
  if (resolve(repo) !== resolve(physical)) throw new Error(`项目必须是独立 Git 仓库：${path}（Git 根目录：${repo}）`);
  return repo;
}

function verifyStaged(args: ReturnType<typeof parseCli>): void {
  const userArg = arg(args, "user-dir");
  if (!userArg) throw new Error("verify-staged 需要 --user-dir");
  const user = resolveRepo(userArg);
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const manifestPath = arg(args, "snapshot") ?? snapshotPath(workspace);
  const snapshot = readJson<ReviewSnapshot>(manifestPath);
  if (resolve(snapshot.userRepo) !== resolve(user)) throw new Error("Review 快照属于另一个 User Project");
  const currentAgentHead = gitText(snapshot.agentRepo, ["rev-parse", "HEAD"]).trim();
  if (currentAgentHead !== snapshot.agentCommit) throw new Error("Agent Project 在 Review 后发生了新提交，必须重新 Review");

  const mismatches: string[] = [];
  for (const rel of snapshot.paths) {
    const entry = getIndexEntry(user, rel);
    if (entry && entry.stage !== "0") throw new Error(`暂存区存在冲突条目：${rel}`);
    const staged = indexState(entry);
    const reviewed = snapshot.userStates[rel];
    if (!statesEqual(staged, reviewed)) mismatches.push(`${rel}（暂存内容与审查快照不同）`);
    const worktree = getWorktreeState(user, rel);
    if (!statesEqual(worktree, staged)) mismatches.push(`${rel}（暂存后工作区又发生变化）`);
  }
  if (mismatches.length > 0) {
    console.log(JSON.stringify({ status: "CHANGED_AFTER_REVIEW", mismatches, snapshot: manifestPath }, null, 2));
    process.exitCode = 2;
    return;
  }
  console.log(JSON.stringify({ status: "VERIFIED", paths: snapshot.paths, snapshot: manifestPath }, null, 2));
}

function printHelp(): void {
  console.log(`review-diff.ts

用法:
  npx tsx review-diff.ts review --user-dir <path> --agent-dir <path> --workspace <workspace> --paths-file <paths.json> [--agent-commit <sha>]
  npx tsx review-diff.ts verify-staged --user-dir <path> --workspace <workspace> [--snapshot <manifest.json>]

review 比较：
- User Project 当前工作区文件状态
- User Project Index 与 Agent Commit 父版本（确认比较基线）
- Agent Project 指定 [agent] Commit 的最终文件状态

EXACT_MATCH：相关文件内容、类型和可识别文件模式一致。
DIFFERENT：基线一致，但用户实现与 Agent 实现不同，需要 AI 审查。
BASELINE_MISMATCH：Index 与 Agent 父版本不一致，不能自动判定。
脚本只读 User Project，不执行 git add、不修改用户文件。`);
}

function main(): void {
  const args = parseCli(process.argv.slice(2));
  const command = args.positionals[0];
  if (!command || command === "--help" || args.values.has("help")) return printHelp();
  if (command === "review") review(args);
  else if (command === "verify-staged") verifyStaged(args);
  else throw new Error(`未知操作：${command}`);
}

const direct = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (direct) main();
