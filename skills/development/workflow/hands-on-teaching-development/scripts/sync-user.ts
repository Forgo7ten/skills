#!/usr/bin/env tsx
import { fileURLToPath } from "node:url";
/**
 * Synchronize an approved User Project implementation into Agent Project,
 * or apply an Agent Project commit to User Project when the user opts out of handwriting.
 */
import { existsSync, realpathSync } from "node:fs";
import { join, resolve } from "node:path";
import {
  arg,
  commitCodePaths,
  copyEntryNoFollow,
  getGitTreeEntry,
  getIndexEntry,
  getTeachingQueueCursor,
  getWorktreeState,
  git,
  gitRoot,
  gitText,
  gitStatus,
  gitWriteTree,
  indexState,
  parseCli,
  parentOrEmptyTree,
  pathIn,
  readExplicitPaths,
  readJson,
  removePath,
  runCli,
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
  decision?: { choice: "user" | "agent"; reason: string; recordedAt: string };
}

function resolveRepo(path: string): string {
  const physical = realpathSync(path);
  const root = gitRoot(physical);
  if (!root || resolve(root) !== resolve(physical)) throw new Error(`项目必须是独立 Git 仓库：${path}`);
  return root;
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
  // A prepared batch may place later [agent] commits between this step and HEAD.
  if (gitStatus(agent, ["merge-base", "--is-ancestor", snapshot.agentCommit, "HEAD"]) !== 0) {
    throw new Error("Review 对应的 Agent Commit 已不在当前历史中，请重新确认教学队列");
  }
  if (snapshot.status === "BASELINE_MISMATCH") {
    throw new Error("Review 显示 User Index 与 Agent 基线不一致；禁止自动吸收，请先恢复或人工核对教学基线。");
  }
  const queueCursor = getTeachingQueueCursor(agent);
  if (queueCursor && (queueCursor.status !== "active" || queueCursor.commit !== snapshot.agentCommit)) {
    throw new Error("教学游标与 Review 对应提交不一致，不能跨步骤吸收用户代码");
  }
  if (snapshot.status === "DIFFERENT" && snapshot.decision?.choice !== "user") {
    throw new Error("Review 存在差异，尚未审计并确认采用用户实现；禁止自动吸收");
  }
  verifySnapshotStaged(user, snapshot);

  // Preflight *all* changed paths before writing anything into Agent Project.
  // Blindly copying a user step over a newer prepared step can erase future work.
  const changedByUser = snapshot.paths.filter((rel) => {
    const expected = treeState(getGitTreeEntry(agent, snapshot.agentCommit, rel));
    return !statesEqual(expected, snapshot.userStates[rel]);
  });
  const conflicts = changedByUser.filter((rel) => {
    // Even when a later commit changed this file and then reverted it,
    // using the old user file could still invalidate future tutorial baselines.
    const touchedByLaterCommit = gitText(agent, ["log", "--format=%H", `${snapshot.agentCommit}..HEAD`, "--", `:(literal)${rel}`]).trim();
    const prepared = treeState(getGitTreeEntry(agent, "HEAD", rel));
    const atStep = treeState(getGitTreeEntry(agent, snapshot.agentCommit, rel));
    return touchedByLaterCommit.length > 0 || !statesEqual(prepared, atStep);
  });
  if (conflicts.length > 0) {
    throw new Error(`用户实现与提前准备的后续提交修改了相同文件；拒绝覆盖 Agent 项目的未来实现：\n${conflicts.join("\n")}\n请暂停当前教学批次、记录纠偏并重新协调剩余步骤。`);
  }
  const outstanding = gitText(agent, ["status", "--porcelain", "--untracked-files=normal"]).trim();
  if (outstanding) throw new Error("Agent Project 有未提交文件，拒绝在不干净的工作区吸收用户代码");

  for (const rel of changedByUser) {
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

  // Only inspect files we actually copied. Exact matches from older prepared steps
  // must not overwrite or be compared against later Agent implementations.
  for (const rel of changedByUser) {
    const actual = getWorktreeState(agent, rel);
    if (!statesEqual(actual, snapshot.userStates[rel])) throw new Error(`同步后校验失败：${rel}`);
  }
  console.log(JSON.stringify({
    status: "ABSORBED",
    paths: snapshot.paths,
    copiedPaths: changedByUser,
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
  if (!userArg || !agentArg) {
    throw new Error("apply-agent 需要 --user-dir 和 --agent-dir");
  }
  const user = resolveRepo(userArg);
  const agent = resolveRepo(agentArg);
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const queueCursor = getTeachingQueueCursor(agent);
  const requestedCommit = arg(args, "agent-commit") ?? queueCursor?.commit;
  if (!requestedCommit) {
    throw new Error("当前没有激活的教学队列，apply-agent 需要提供 --agent-commit <sha>");
  }
  const commit = gitText(agent, ["rev-parse", `${requestedCommit}^{commit}`]).trim();
  if (queueCursor && (queueCursor.status !== "active" || queueCursor.commit !== commit)) {
    throw new Error(`只能移植当前待学步骤 ${queueCursor.stepId} (${queueCursor.commit})，不能跳步或在批次暂停时移植`);
  }
  if (gitStatus(agent, ["merge-base", "--is-ancestor", commit, "HEAD"]) !== 0) {
    throw new Error(`Agent Commit 不在当前历史中：${commit}`);
  }
  const subject = gitText(agent, ["show", "-s", "--format=%s", commit]).trim();
  if (!subject.startsWith("[agent]")) throw new Error(`只能移植 [agent] Commit，当前提交为：${subject}`);
  const parent = parentOrEmptyTree(agent, commit);
  const explicitPaths = readExplicitPaths(args);
  const paths = explicitPaths ?? (queueCursor && queueCursor.commit === commit && queueCursor.paths.length ? queueCursor.paths : commitCodePaths(agent, commit));
  if (paths.length === 0) throw new Error("没有需要移植的文件路径；请通过 --path 或 --paths-file 指定");

  // If this step was already reviewed as DIFFERENT, enforce the user's recorded choice.
  // Direct acceptance without an existing review remains supported.
  const reviewPath = join(workspace, ".teaching", "review", "latest.json");
  if (existsSync(reviewPath)) {
    const recorded = readJson<ReviewSnapshot>(reviewPath);
    if (recorded.agentCommit === commit && resolve(recorded.agentRepo) === agent &&
        resolve(recorded.userRepo) === user && recorded.status === "DIFFERENT") {
      if (recorded.decision?.choice !== "agent") {
        throw new Error("当前步骤有尚未明确选择 Agent 方案的差异 Review，禁止自动覆盖用户代码");
      }
      const changedSinceReview = recorded.paths.filter((rel) =>
        !statesEqual(getWorktreeState(user, rel), recorded.userStates[rel]));
      if (changedSinceReview.length) {
        throw new Error(`用户代码在确认采用 Agent 方案后发生变更，必须重新 Review：${changedSinceReview.join(", ")}`);
      }
    }
  }

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

1. 吸收已 Review 并精确暂存的用户实现：
   npx tsx sync-user.ts absorb --user-dir <path> --agent-dir <path> --workspace <workspace> [--snapshot <latest.json>]

2. 用户选择直接接受 Agent 实现：
   npx tsx sync-user.ts apply-agent --user-dir <path> --agent-dir <path> --workspace <workspace> [--agent-commit <sha>] [--path <file>...] [--paths-file <paths.json>]

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
if (direct) runCli(main);
