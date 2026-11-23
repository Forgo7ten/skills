#!/usr/bin/env tsx
import { fileURLToPath } from "node:url";
/**
 * Compare the User Project worktree implementation with one Agent Project commit.
 * The User Project index is treated as the baseline; no files or index entries are changed.
 */
import { chmodSync, mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import {
  arg,
  commitCodePaths,
  copyEntryNoFollow,
  getGitTreeEntry,
  getIndexEntry,
  getTeachingQueueCursor,
  getWorktreeState,
  git,
  gitWriteTree,
  run,
  runCli,
  gitRoot,
  gitText,
  gitStatus,
  indexState,
  parseCli,
  parseNul,
  parentOrEmptyTree,
  readExplicitPaths,
  readJson,
  safeRelativePath,
  pathIn,
  statesEqual,
  ensureParent,
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
  decision?: {
    choice: "user" | "agent";
    reason: string;
    recommended?: "agent" | "user";
    inspiration?: string;
    recordedAt: string;
  };
}

function resolveAgentCommit(agent: string, requested?: string): string {
  const queued = getTeachingQueueCursor(agent);
  if (queued && queued.status !== "active") throw new Error(`教学队列当前不可审查（${queued.status}）；请先核对教学进度`);
  const commit = requested ?? queued?.commit ?? gitText(agent, ["log", "-1", "--format=%H", "--grep=^\\[agent\\]"]).trim();
  if (!commit) throw new Error("找不到 Agent Project 最近的 [agent] Commit");
  const subject = gitText(agent, ["show", "-s", "--format=%s", commit]).trim();
  if (!subject.startsWith("[agent]")) throw new Error(`指定提交不是 [agent] Commit：${subject}`);
  const sha = gitText(agent, ["rev-parse", `${commit}^{commit}`]).trim();
  if (queued && sha !== queued.commit) throw new Error(`当前教学游标为 ${queued.stepId} (${queued.commit})，禁止跳步 Review：${sha}`);
  return sha;
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

/** Git compares selected commit blobs to the actual User worktree, not Agent HEAD.
 * Keep the temporary copies outside both repositories and remove them in finally.
 */
function comparisonDiff(
  user: string, agent: string, commit: string, paths: string[], reportDir: string,
): { patch: Buffer; whitespaceOnlyCandidates: string[] } {
  const temp = mkdtempSync(join(reportDir, ".comparison-"));
  const patches: Buffer[] = [];
  const whitespaceOnlyCandidates: string[] = [];
  try {
    for (const rel of paths) {
      const expected = getGitTreeEntry(agent, commit, rel);
      const actual = getWorktreeState(user, rel);
      const left = pathIn(temp, `agent/${rel}`);
      const right = pathIn(temp, `user/${rel}`);
      if (expected) {
        if (expected.type !== "blob") throw new Error(`当前步骤不支持比较目录对象：${rel}`);
        ensureParent(left);
        const bytes = git(agent, ["cat-file", "blob", expected.hash]);
        if (expected.mode === "120000") symlinkSync(bytes.toString("utf8"), left);
        else {
          writeFileSync(left, bytes);
          chmodSync(left, expected.mode === "100755" ? 0o755 : 0o644);
        }
      }
      if (actual.exists) copyEntryNoFollow(pathIn(user, rel), right);
      const a = expected ? `agent/${rel}` : "/dev/null";
      const b = actual.exists ? `user/${rel}` : "/dev/null";
      if (!expected && !actual.exists) continue;
      const opts = { cwd: temp, allowCodes: [0, 1] };
      const basic = run("git", ["diff", "--no-index", "--no-ext-diff", "--no-color", "--", a, b], opts);
      patches.push(basic.stdout);
      if (basic.status === 1 && expected?.mode !== "120000" && actual.type !== "symlink" &&
          (!actual.modeComparable || expected?.mode === actual.mode)) {
        const ignored = run("git", ["diff", "--no-index", "-w", "--no-ext-diff", "--no-color", "--", a, b], opts);
        if (ignored.status === 0) whitespaceOnlyCandidates.push(rel);
      }
    }
    return { patch: Buffer.concat(patches), whitespaceOnlyCandidates };
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
}

function inlineDiff(bytes: Buffer, maxBytes: number): { text: string; bytes: number; truncated: boolean } {
  const truncated = bytes.length > maxBytes;
  return {
    text: (truncated ? bytes.subarray(0, maxBytes) : bytes).toString("utf8"),
    bytes: bytes.length,
    truncated,
  };
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
  // The Agent may prepare several commits ahead: review the selected queued commit, not HEAD.
  if (gitStatus(agent, ["merge-base", "--is-ancestor", commit, "HEAD"]) !== 0) {
    throw new Error(`被审查的 Agent Commit 已不在当前历史中：${commit}`);
  }
  const parent = parentOrEmptyTree(agent, commit);
  const queued = getTeachingQueueCursor(agent);
  const explicitPaths = readExplicitPaths(args);
  const paths = explicitPaths ?? (queued && queued.commit === commit && queued.paths.length ? queued.paths : commitCodePaths(agent, commit));
  if (paths.length === 0) throw new Error("当前 Agent Commit 没有可审查的代码文件；请通过 --path 或 --paths-file 指定范围");

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
  const compared = comparisonDiff(user, agent, commit, paths, reportDir);
  writeFileSync(join(reportDir, "user-vs-agent.diff"), compared.patch);
  writeFileSync(join(reportDir, "agent.patch"), agentPatch);
  writeFileSync(join(reportDir, "user.patch"), userPatch);
  writeFileSync(join(reportDir, "user-extra.patch"), extraUserPatch);
  writeFileSync(join(reportDir, "paths.json"), `${JSON.stringify(paths, null, 2)}\n`);
  writeFileSync(join(reportDir, "extra-user-paths.json"), `${JSON.stringify(extraUserPaths, null, 2)}\n`);

  const maxInlineBytes = Number(arg(args, "max-inline-bytes") ?? "131072");
  if (!Number.isSafeInteger(maxInlineBytes) || maxInlineBytes < 1024 || maxInlineBytes > 2097152) {
    throw new Error("--max-inline-bytes 需介于 1024 和 2097152 之间");
  }
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
    whitespaceOnlyCandidates: compared.whitespaceOnlyCandidates,
    // Inline diff eliminates a second lookup in normal cases. If capped, report paths remain available.
    diffs: status === "EXACT_MATCH" ? null : {
      userVsAgent: inlineDiff(compared.patch, maxInlineBytes),
      userPatch: inlineDiff(userPatch, maxInlineBytes),
      agentPatch: inlineDiff(agentPatch, maxInlineBytes),
      extraUserPatch: inlineDiff(extraUserPatch, maxInlineBytes),
    },
    snapshot: manifestPath,
    reports: {
      agentPatch: join(reportDir, "agent.patch"),
      userVsAgent: join(reportDir, "user-vs-agent.diff"),
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
  if (gitStatus(snapshot.agentRepo, ["merge-base", "--is-ancestor", snapshot.agentCommit, currentAgentHead]) !== 0) {
    throw new Error("被审查的 Agent Commit 不再属于当前 Git 历史，请重新 Review");
  }
  const queued = getTeachingQueueCursor(snapshot.agentRepo);
  if (queued && (queued.status !== "active" || queued.commit !== snapshot.agentCommit)) {
    throw new Error("教学游标已改变或批次暂停，不能复用旧 Review 快照");
  }

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

/** An Agent records the user's informed selection after auditing DIFFERENT. */
function decide(args: ReturnType<typeof parseCli>): void {
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const manifest = resolve(arg(args, "snapshot") ?? snapshotPath(workspace));
  const snapshot = readJson<ReviewSnapshot>(manifest);
  if (snapshot.status !== "DIFFERENT") throw new Error("只有 DIFFERENT Review 需要选择方案");
  const choice = arg(args, "choice");
  const reason = arg(args, "reason")?.trim();
  const recommended = arg(args, "recommend");
  const inspiration = arg(args, "inspiration")?.trim();
  if ((choice !== "user" && choice !== "agent") || !reason) {
    throw new Error("需要 --choice user|agent 和 --reason（审计结论及用户选择）");
  }
  if (recommended !== undefined && recommended !== "agent" && recommended !== "user") {
    throw new Error("--recommend 必须为 agent 或 user");
  }
  // Check the selected Git commit is still in this repo's current history.
  if (gitStatus(snapshot.agentRepo, ["merge-base", "--is-ancestor", snapshot.agentCommit, "HEAD"]) !== 0) {
    throw new Error("Agent 历史已变化，请重新 Review");
  }
  const queued = getTeachingQueueCursor(snapshot.agentRepo);
  if (queued && (queued.status !== "active" || queued.commit !== snapshot.agentCommit)) {
    throw new Error("教学游标已改变，请重新 Review");
  }
  snapshot.decision = {
    choice,
    reason,
    ...(recommended ? { recommended } : {}),
    ...(inspiration ? { inspiration } : {}),
    recordedAt: new Date().toISOString(),
  };
  writeJson(manifest, snapshot);
  console.log(JSON.stringify({ status: "DECIDED", decision: snapshot.decision, choice, reason, snapshot: manifest }, null, 2));
}

/** Stage exactly the reviewed files; never run git add . or modify source files.
 * If index/worktree changed since the review, fail without staging anything.
 */
function stageReviewed(args: ReturnType<typeof parseCli>): void {
  const userDir = arg(args, "user-dir");
  if (!userDir) throw new Error("stage-reviewed 需要 --user-dir");
  const user = resolveRepo(userDir);
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const manifest = resolve(arg(args, "snapshot") ?? snapshotPath(workspace));
  const snapshot = readJson<ReviewSnapshot>(manifest);
  if (resolve(snapshot.userRepo) !== user) throw new Error("Review 快照不属于当前 User Project");
  if (snapshot.status === "BASELINE_MISMATCH") throw new Error("基线不一致，不能暂存");
  if (snapshot.status === "DIFFERENT" && snapshot.decision?.choice !== "user") {
    throw new Error("用户实现与 Agent 不同：先审计、询问用户，再记录 --choice user 才能暂存");
  }
  if (gitStatus(snapshot.agentRepo, ["merge-base", "--is-ancestor", snapshot.agentCommit, "HEAD"]) !== 0) {
    throw new Error("Agent Commit 不再属于当前历史，请重新 Review");
  }
  const queued = getTeachingQueueCursor(snapshot.agentRepo);
  if (queued && (queued.status !== "active" || queued.commit !== snapshot.agentCommit)) {
    throw new Error("教学队列游标已变化，请重新 Review");
  }
  const stale: string[] = [];
  for (const rel of snapshot.paths) {
    const entry = getIndexEntry(user, rel);
    if (entry && entry.stage !== "0") throw new Error(`暂存区有冲突：${rel}`);
    if (!statesEqual(indexState(entry), snapshot.baselineStates[rel]) ||
        !statesEqual(getWorktreeState(user, rel), snapshot.userStates[rel])) stale.push(rel);
  }
  if (stale.length) throw new Error(`Review 后文件或暂存区已变更，请重新审查：${stale.join(", ")}`);
  const originalIndex = gitWriteTree(user);
  try {
    git(user, ["add", "-A", "--", ...snapshot.paths.map((rel) => `:(literal)${rel}`)]);
    const errors: string[] = [];
    for (const rel of snapshot.paths) {
      const staged = indexState(getIndexEntry(user, rel));
      if (!statesEqual(staged, snapshot.userStates[rel]) ||
          !statesEqual(getWorktreeState(user, rel), staged)) errors.push(rel);
    }
    if (errors.length) throw new Error(`暂存验证失败：${errors.join(", ")}`);
  } catch (error) {
    git(user, ["read-tree", "--reset", originalIndex]);
    throw error;
  }
  console.log(JSON.stringify({ status: "STAGED", paths: snapshot.paths, snapshot: manifest }, null, 2));
}

function printHelp(): void {
  console.log(`review-diff.ts

用法:
  npx tsx review-diff.ts review --user-dir <path> --agent-dir <path> --workspace <workspace> [--agent-commit <sha>] [--path <file>...] [--paths-file <paths.json>]
  npx tsx review-diff.ts decide --workspace <workspace> --choice user|agent --reason <审计与用户确认> [--recommend agent|user] [--inspiration <用户带来的新启发>] [--snapshot <manifest.json>]
  npx tsx review-diff.ts stage-reviewed --user-dir <path> --workspace <workspace> [--snapshot <manifest.json>]
  npx tsx review-diff.ts verify-staged --user-dir <path> --workspace <workspace> [--snapshot <manifest.json>]

review 比较：
- User Project 当前工作区文件状态
- User Project Index 与 Agent Commit 父版本（确认比较基线）
- Agent Project 指定排队 [agent] Commit 的最终文件状态（不要求是 HEAD）

EXACT_MATCH：相关文件内容、类型和可识别文件模式一致。
DIFFERENT：基线一致，但用户实现与 Agent 实现不同，需要 AI 审查。
BASELINE_MISMATCH：Index 与 Agent 父版本不一致，不能自动判定。
review/decide/verify-staged 不修改用户文件或暂存区；stage-reviewed 仅暂存审查快照中的精确路径并自带暂存后校验。
DIFFERENT 须经审计并由用户确认选择；-w 仅提示空白差异候选，不能据此断定在 Python 等语言中无行为变化。
默认在一次 JSON 输出中附带差异，超大补丁显式标记 truncated 并保留报告文件。`);
}

function main(): void {
  const args = parseCli(process.argv.slice(2));
  const command = args.positionals[0];
  if (!command || command === "--help" || args.values.has("help")) return printHelp();
  if (command === "review") review(args);
  else if (command === "verify-staged") verifyStaged(args);
  else if (command === "decide") decide(args);
  else if (command === "stage-reviewed") stageReviewed(args);
  else throw new Error(`未知操作：${command}`);
}

const direct = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (direct) runCli(main);
