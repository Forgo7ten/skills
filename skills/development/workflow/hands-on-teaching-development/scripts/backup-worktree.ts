#!/usr/bin/env tsx
import { fileURLToPath } from "node:url";
/**
 * Back up, clear, and restore only User Project worktree changes.
 * The Git index (staged content) is never cleared or rewritten.
 */
import {
  copyEntryNoFollow,
  git,
  gitRoot,
  gitStatus,
  gitWriteTree,
  parseNul,
  parseCli,
  arg,
  pathIn,
  readJson,
  runCli,
  writeJson,
} from "./common.ts";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readlinkSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";

export interface WorktreeBackupManifest {
  version: 1;
  createdAt: string;
  repoRoot: string;
  indexTree: string;
  head: string | null;
  trackedPatch: string;
  untrackedPaths: string[];
}

function defaultBackupDir(workspace: string): string {
  return join(resolve(workspace), ".teaching", "backup", "current");
}

function pathExistsIncludingDangling(path: string): boolean {
  try {
    lstatSync(path);
    return true;
  } catch {
    return false;
  }
}

function listBackupUntrackedPaths(repo: string): string[] {
  // Embedded untracked Git repos are reported with a trailing `/`; `git clean -fd` does not remove them either.
  return parseNul(git(repo, ["ls-files", "--others", "--exclude-standard", "-z"])).filter((rel) => !rel.endsWith("/"));
}

function hasUnstagedChanges(repo: string): boolean {
  const trackedChanged = gitStatus(repo, ["diff", "--quiet"]) !== 0;
  return trackedChanged || listBackupUntrackedPaths(repo).length > 0;
}

function isSameEntry(source: string, destination: string): boolean {
  try {
    const a = lstatSync(source);
    const b = lstatSync(destination);
    if (a.isSymbolicLink() || b.isSymbolicLink()) {
      return a.isSymbolicLink() && b.isSymbolicLink() && readlinkSync(source) === readlinkSync(destination);
    }
    if (!a.isFile() || !b.isFile()) return false;
    return readFileSync(source).equals(readFileSync(destination));
  } catch {
    return false;
  }
}

export function createWorktreeBackup(userPath: string, workspace: string): { backupDir: string; created: boolean } {
  const physical = realpathSync(userPath);
  const repo = gitRoot(physical);
  if (!repo || resolve(repo) !== resolve(physical)) throw new Error("User Project 必须是独立 Git 仓库");
  if (!hasUnstagedChanges(repo)) return { backupDir: defaultBackupDir(workspace), created: false };

  const backupDir = defaultBackupDir(workspace);
  const parent = dirname(backupDir);
  mkdirSync(parent, { recursive: true });
  const temp = `${backupDir}.tmp-${process.pid}`;
  const previous = `${backupDir}.previous-${process.pid}`;
  rmSync(temp, { recursive: true, force: true });
  rmSync(previous, { recursive: true, force: true });
  mkdirSync(join(temp, "untracked"), { recursive: true });

  const patch = git(repo, ["diff", "--binary", "--no-ext-diff", "--no-color"]);
  writeFileSync(join(temp, "tracked.patch"), patch);
  const untrackedPaths = listBackupUntrackedPaths(repo);
  for (const rel of untrackedPaths) {
    const source = pathIn(repo, rel);
    const destination = pathIn(join(temp, "untracked"), rel);
    copyEntryNoFollow(source, destination);
  }

  const headResult = git(repo, ["rev-parse", "--verify", "HEAD"], { allowCodes: [0, 128] });
  const manifest: WorktreeBackupManifest = {
    version: 1,
    createdAt: new Date().toISOString(),
    repoRoot: repo,
    indexTree: gitWriteTree(repo),
    head: headResult.length ? headResult.toString("utf8").trim() : null,
    trackedPatch: "tracked.patch",
    untrackedPaths,
  };
  writeJson(join(temp, "manifest.json"), manifest);

  // Verify the backup is readable before replacing the previous snapshot.
  const check = readJson<WorktreeBackupManifest>(join(temp, "manifest.json"));
  if (check.indexTree !== manifest.indexTree || check.untrackedPaths.length !== untrackedPaths.length) {
    rmSync(temp, { recursive: true, force: true });
    throw new Error("工作区备份校验失败，原备份未被替换");
  }

  let movedPrevious = false;
  try {
    if (existsSync(backupDir)) {
      renameSync(backupDir, previous);
      movedPrevious = true;
    }
    renameSync(temp, backupDir);
    if (movedPrevious) rmSync(previous, { recursive: true, force: true });
  } catch (error) {
    if (movedPrevious && !existsSync(backupDir) && existsSync(previous)) renameSync(previous, backupDir);
    throw error;
  }
  return { backupDir, created: true };
}

export function restoreWorktreeBackup(userPath: string, workspace: string): void {
  const physical = realpathSync(userPath);
  const repo = gitRoot(physical);
  if (!repo || resolve(repo) !== resolve(physical)) throw new Error("User Project 必须是独立 Git 仓库");
  const backupDir = defaultBackupDir(workspace);
  const manifestPath = join(backupDir, "manifest.json");
  if (!existsSync(manifestPath)) throw new Error(`没有可恢复的工作区备份：${backupDir}`);
  const manifest = readJson<WorktreeBackupManifest>(manifestPath);
  if (resolve(manifest.repoRoot) !== resolve(repo)) throw new Error("备份属于另一个 User Project，拒绝恢复");
  const indexChanged = gitWriteTree(repo) !== manifest.indexTree;

  const patchPath = join(backupDir, manifest.trackedPatch);
  const patch = readFileSync(patchPath);
  if (patch.length > 0) {
    // If the Index changed after backup, only restore when the saved patch still applies cleanly.
    // This avoids blindly overwriting newer staged work while still allowing recovery after a Patch apply.
    git(repo, ["apply", "--check", "--binary", "-"], { input: patch });
  }
  for (const rel of manifest.untrackedPaths) {
    const source = pathIn(join(backupDir, "untracked"), rel);
    const destination = pathIn(repo, rel);
    if (pathExistsIncludingDangling(destination)) {
      if (!isSameEntry(source, destination)) throw new Error(`恢复会覆盖现有文件，已停止：${rel}`);
    }
  }

  if (patch.length > 0) git(repo, ["apply", "--binary", "-"], { input: patch });
  for (const rel of manifest.untrackedPaths) {
    const source = pathIn(join(backupDir, "untracked"), rel);
    const destination = pathIn(repo, rel);
    if (!pathExistsIncludingDangling(destination)) copyEntryNoFollow(source, destination);
  }
  console.log(indexChanged
    ? `已在当前 Index 基础上恢复工作区备份（Index 已变化，但 Patch 可无冲突应用）：${backupDir}`
    : `已恢复工作区备份：${backupDir}`);
}

export function clearWorktreeWithBackup(userPath: string, workspace: string): { backupDir: string; backedUp: boolean } {
  const physical = realpathSync(userPath);
  const repo = gitRoot(physical);
  if (!repo || resolve(repo) !== resolve(physical)) throw new Error("User Project 必须是独立 Git 仓库");
  const backup = createWorktreeBackup(repo, workspace);
  if (!backup.created) return { backupDir: backup.backupDir, backedUp: false };
  try {
    // Clean while the user's current .gitignore is still present, so files currently
    // ignored by uncommitted ignore-rule edits are not accidentally deleted.
    git(repo, ["clean", "-fd", "--", "."]);
    git(repo, ["restore", "--worktree", "--", "."]);
  } catch (error) {
    try {
      restoreWorktreeBackup(repo, workspace);
    } catch (restoreError) {
      throw new Error(`清理工作区失败：${String(error)}\n自动恢复也失败：${String(restoreError)}\n备份仍保留在：${backup.backupDir}`);
    }
    throw error;
  }
  return { backupDir: backup.backupDir, backedUp: true };
}

function printHelp(): void {
  console.log(`backup-worktree.ts

用法:
  npx tsx backup-worktree.ts create --user-dir <path> --workspace <path>
  npx tsx backup-worktree.ts clear  --user-dir <path> --workspace <path>
  npx tsx backup-worktree.ts restore --user-dir <path> --workspace <path>

只备份未暂存的已跟踪文件修改和未跟踪文件；不处理已暂存内容和 Git 忽略文件。清理前强制备份。`);
}

function main(): void {
  const args = parseCli(process.argv.slice(2));
  const action = args.positionals[0];
  if (!action || action === "--help" || args.values.has("help")) return printHelp();
  const userDir = resolve(arg(args, "user-dir") ?? "");
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  if (!arg(args, "user-dir")) throw new Error("缺少 --user-dir");
  if (action === "create") {
    const result = createWorktreeBackup(userDir, workspace);
    console.log(result.created ? `备份完成：${result.backupDir}` : "工作区没有未暂存修改，无需备份。");
  } else if (action === "clear") {
    const result = clearWorktreeWithBackup(userDir, workspace);
    console.log(result.backedUp ? `已备份并清理工作区：${result.backupDir}` : "工作区干净，无需清理。");
  } else if (action === "restore") {
    restoreWorktreeBackup(userDir, workspace);
  } else {
    throw new Error(`未知操作：${action}`);
  }
}

const direct = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (direct) runCli(main);
