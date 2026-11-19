#!/usr/bin/env tsx
import { fileURLToPath } from "node:url";
/**
 * Synchronize an approved User Project implementation into Agent Project,
 * or apply an Agent Project commit to User Project when the user opts out of handwriting.
 */
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import {
  arg,
  copyEntryNoFollow,
  ensureParent,
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
  gitWriteTree,
  pathIn,
  readJson,
  removePath,
  safeRelativePath,
  statesEqual,
  treeState,
  type FileState,
} from "./common.ts";
import { clearWorktreeWithBackup, restoreWorktreeBackup } from "./backup-worktree.ts";

interface ReviewSnapshot {
  version: 1;
  status: string;
  userRepo: string;
  agentRepo: string;
  agentCommit: string;
  agentParent: string;
  paths: string[];
  userStates: Record<string, FileState>;
}

function resolveRepo(path: string): string {
  const physical = realpathSync(path);
  const root = gitRoot(physical);
  if (!root || resolve(root) !== resolve(physical)) throw new Error(`项目必须是独立 Git 仓库：${path}`);
  return root;
}

function readPathsFile(path: string): string[] {
  const parsed = JSON.parse(readFileSync(path, "utf8")) as unknown;
  const values = Array.isArray(parsed) ? parsed : (parsed as { files?: unknown })?.files;
  if (!Array.isArray(values) || !values.every((item) => typeof item === "string")) {
    throw new Error(`paths-file 必须是字符串数组，或包含 files 字符串数组的 JSON 对象：${path}`);
  }
  return [...new Set(values.map((item) => safeRelativePath(item as string)))].sort();
}

function loadSnapshot(args: ReturnType<typeof parseCli>, workspace: string): { path: string; snapshot: ReviewSnapshot } {
  const path = resolve(arg(args, "snapshot") ?? join(workspace, ".teaching", "review", "latest.json"));
  if (!existsSync(path)) throw new Error(`Review 快照不存在：${path}`);
  return { path, snapshot: readJson<ReviewSnapshot>(path) };
}

function verifySnapshotStaged(user: string, snapshot: ReviewSnapshot): void {
  for (const rel of snapshot.paths) {
    const entry = getIndexEntry(user, rel);
    if (entry && entry.stage !== "0") throw new Error(`User Project 暂存区存在冲突条目：${rel}`);
    const staged = indexState(entry);
    if (!statesEqual(staged, snapshot.userStates[rel])) throw new Error(`暂存区内容与已审查快照不一致：${rel}`);
    const worktree = getWorktreeState(user, rel);
    if (!statesEqual(worktree, staged)) throw new Error(`暂存后工作区又发生变化：${rel}`);
  }
}

function absorb(args: ReturnType<typeof parseCli>): void {
  const userArg = arg(args, "user-dir");
  const agentArg = arg(args, "agent-dir");
  if (!userArg || !agentArg) throw new Error("absorb 需要 --user-dir 和 --agent-dir");
  const user = resolveRepo(userArg);
  const agent = resolveRepo(agentArg);
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const { path: snapshotPath, snapshot } = loadSnapshot(args, workspace);
  if (resolve(snapshot.userRepo) !== resolve(user) || resolve(snapshot.agentRepo) !== resolve(agent)) {
    throw new Error("Review 快照与当前 User Project / Agent Project 不匹配");
  }
  if (gitText(agent, ["rev-parse", "HEAD"]).trim() !== snapshot.agentCommit) {
    throw new Error("Agent Project 在 Review 后发生了新提交，不能直接吸收；请重新确认当前步骤");
  }
  if (snapshot.status === "BASELINE_MISMATCH") {
    throw new Error("Review 显示 User Index 与 Agent 基线不一致；禁止自动吸收，请先恢复或人工核对教学基线。");
  }
  verifySnapshotStaged(user, snapshot);

  for (const rel of snapshot.paths) {
    const source = pathIn(user, rel);
    const destination = pathIn(agent, rel);
    const state = snapshot.userStates[rel];
    if (!state.exists) {
      removePath(destination);
      continue;
    }
    removePath(destination);
    copyEntryNoFollow(source, destination);
  }

  // Verify the copied Agent Project files match the exact reviewed User Project snapshot.
  for (const rel of snapshot.paths) {
    const actual = getWorktreeState(agent, rel);
    if (!statesEqual(actual, snapshot.userStates[rel])) throw new Error(`同步后校验失败：${rel}`);
  }
  console.log(JSON.stringify({
    status: "ABSORBED",
    paths: snapshot.paths,
    snapshot: snapshotPath,
    note: "代码已同步到 Agent Project；请更新 development-log.md / development-plan.md / teaching-state.md，再使用 checkpoint.ts 创建 [user] Commit。",
  }, null, 2));
}

/**
 * Undo an Agent patch without discarding previously staged User steps.
 * `git reset` (mixed) is NOT safe: it would reset the teaching baseline to HEAD.
 * Restore the exact pre-apply index tree, then files, then the saved unstaged work.
 */
export function rollbackFailedAgentApply(
  user: string,
  workspace: string,
  preApplyIndexTree: string,
  paths: string[],
  backedUp: boolean,
): void {
  git(user, ["read-tree", "--reset", preApplyIndexTree]);
  for (const rel of paths) {
    const entry = getIndexEntry(user, rel);
    if (entry) {
      git(user, ["restore", "--worktree", "--", `:(literal)${rel}`]);
    } else {
      // This path was added by the patch and did not exist in the original Index.
      removePath(pathIn(user, rel));
    }
  }
  if (backedUp) restoreWorktreeBackup(user, workspace);
  if (gitWriteTree(user) !== preApplyIndexTree) throw new Error("回滚后 Index Tree 与应用前不一致");
}

function applyAgent(args: ReturnType<typeof parseCli>): void {
  const userArg = arg(args, "user-dir");
  const agentArg = arg(args, "agent-dir");
  const commitArg = arg(args, "agent-commit");
  const pathsFile = arg(args, "paths-file");
  if (!userArg || !agentArg || !commitArg || !pathsFile) {
    throw new Error("apply-agent 需要 --user-dir、--agent-dir、--agent-commit 和 --paths-file");
  }
  const user = resolveRepo(userArg);
  const agent = resolveRepo(agentArg);
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const commit = gitText(agent, ["rev-parse", `${commitArg}^{commit}`]).trim();
  const subject = gitText(agent, ["show", "-s", "--format=%s", commit]).trim();
  if (!subject.startsWith("[agent]")) throw new Error(`只能移植 [agent] Commit，当前提交为：${subject}`);
  const parent = parentOrEmptyTree(agent, commit);
  const paths = readPathsFile(pathsFile);
  if (paths.length === 0) throw new Error("没有需要移植的文件路径");

  // Before cleaning anything, ensure the User Project index is the expected base.
  const baselineMismatch: string[] = [];
  for (const rel of paths) {
    const base = treeState(getGitTreeEntry(agent, parent, rel));
    const entry = getIndexEntry(user, rel);
    if (entry && entry.stage !== "0") throw new Error(`User Project 暂存区存在冲突条目：${rel}`);
    if (!statesEqual(base, indexState(entry))) baselineMismatch.push(rel);
  }
  if (baselineMismatch.length > 0) {
    throw new Error(`User Project 暂存区与 Agent Commit 父版本不一致，拒绝自动移植：\n${baselineMismatch.join("\n")}\n请先检查是否有尚未同步的用户实现。`);
  }

  // Default policy: unless the user explicitly asked to preserve a half-finished implementation,
  // back it up and clear only the unstaged worktree. Staged prior steps remain untouched.
  const preApplyIndexTree = gitWriteTree(user);
  const backup = clearWorktreeWithBackup(user, workspace);
  const pathspecs = paths.map((path) => `:(literal)${path}`);
  const patch = git(agent, ["diff", "--binary", "--no-ext-diff", "--no-color", parent, commit, "--", ...pathspecs]);
  try {
    if (patch.length > 0) {
      git(user, ["apply", "--check", "--index", "--binary", "-"], { input: patch });
      git(user, ["apply", "--index", "--binary", "-"], { input: patch });
    }
    for (const rel of paths) {
      const expected = treeState(getGitTreeEntry(agent, commit, rel));
      const staged = indexState(getIndexEntry(user, rel));
      const worktree = getWorktreeState(user, rel);
      if (!statesEqual(staged, expected) || !statesEqual(worktree, expected)) {
        throw new Error(`Patch 应用后校验失败：${rel}`);
      }
    }
  } catch (error) {
    try {
      rollbackFailedAgentApply(user, workspace, preApplyIndexTree, paths, backup.backedUp);
    } catch (restoreError) {
      throw new Error(`Agent Patch 应用失败：${String(error)}\nIndex/工作区回滚失败：${String(restoreError)}\n请停止操作，手动检查用户暂存区及备份：${backup.backupDir}`);
    }
    throw error;
  }

  console.log(JSON.stringify({
    status: "APPLIED",
    agentCommit: commit,
    paths,
    backup: backup.backedUp ? backup.backupDir : null,
    userIndexUpdated: true,
    note: "Agent 实现已应用并暂存到 User Project。请更新 Agent Project 教学日志，再创建 [user] Commit 记录用户接受。",
  }, null, 2));
}

function printHelp(): void {
  console.log(`sync-user.ts

用法：

1. 吸收已经 Review 并由用户 git add 的实现：
   npx tsx sync-user.ts absorb --user-dir <path> --agent-dir <path> --workspace <workspace> [--snapshot <latest.json>]

2. 用户选择直接接受 Agent 实现：
   npx tsx sync-user.ts apply-agent --user-dir <path> --agent-dir <path> --workspace <workspace> --agent-commit <sha> --paths-file <paths.json>

absorb 会验证暂存区与 Review 快照一致，只同步指定步骤文件，不创建 Commit。
apply-agent 默认先备份并清理 User Project 未暂存工作区，保留暂存区，然后通过 git apply --index 应用 Agent Patch。
如果用户明确要求保留半成品，不要运行 apply-agent；应由 AI 分析并合并。`);
}

function main(): void {
  const args = parseCli(process.argv.slice(2));
  const command = args.positionals[0];
  if (!command || command === "--help" || args.values.has("help")) return printHelp();
  if (command === "absorb") absorb(args);
  else if (command === "apply-agent") applyAgent(args);
  else throw new Error(`未知操作：${command}`);
}

const direct = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (direct) main();
