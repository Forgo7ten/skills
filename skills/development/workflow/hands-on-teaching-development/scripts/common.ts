import {
  chmodSync,
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readlinkSync,
  realpathSync,
  readdirSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";

export interface CliArgs {
  positionals: string[];
  values: Map<string, string[]>;
}

export function parseCli(argv: string[]): CliArgs {
  const positionals: string[] = [];
  const values = new Map<string, string[]>();
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) {
      positionals.push(token);
      continue;
    }
    const equal = token.indexOf("=");
    const key = equal >= 0 ? token.slice(2, equal) : token.slice(2);
    let value: string;
    if (equal >= 0) {
      value = token.slice(equal + 1);
    } else if (i + 1 < argv.length && !argv[i + 1].startsWith("--")) {
      value = argv[i + 1];
      i += 1;
    } else {
      value = "true";
    }
    const list = values.get(key) ?? [];
    list.push(value);
    values.set(key, list);
  }
  return { positionals, values };
}

export function arg(args: CliArgs, key: string, fallback?: string): string | undefined {
  const values = args.values.get(key);
  return values?.[values.length - 1] ?? fallback;
}

export function argsAll(args: CliArgs, key: string): string[] {
  return args.values.get(key) ?? [];
}

export function hasArg(args: CliArgs, key: string): boolean {
  return args.values.has(key);
}

export function requireArg(args: CliArgs, key: string): string {
  const value = arg(args, key);
  if (!value || value === "true") throw new Error(`缺少参数 --${key}`);
  return value;
}

/** Read paths from repeated `--path` flags and/or `--paths-file <json>`. Returns undefined if neither is given. */
export function readExplicitPaths(args: CliArgs): string[] | undefined {
  const inline = argsAll(args, "path");
  const file = arg(args, "paths-file");
  if (!file && inline.length === 0) return undefined;
  const values = [...inline];
  if (file) {
    const parsed = JSON.parse(readFileSync(file, "utf8")) as unknown;
    const fromFile = Array.isArray(parsed) ? parsed : (parsed as { files?: unknown })?.files;
    if (!Array.isArray(fromFile) || !fromFile.every((item) => typeof item === "string")) {
      throw new Error(`paths-file 必须是字符串数组，或包含 files 字符串数组的 JSON 对象：${file}`);
    }
    values.push(...(fromFile as string[]));
  }
  return [...new Set(values.map((item) => safeRelativePath(item)))].sort();
}

export function runCli(fn: () => void): void {
  try {
    fn();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}

export function run(command: string, argv: string[], options: { cwd?: string; input?: Buffer | string; allowCodes?: number[] } = {}) {
  const result = spawnSync(command, argv, {
    cwd: options.cwd,
    input: options.input,
    encoding: undefined,
    maxBuffer: 1024 * 1024 * 128,
    windowsHide: true,
  });
  if (result.error) throw result.error;
  const status = result.status ?? 1;
  const stdout = Buffer.isBuffer(result.stdout) ? result.stdout : Buffer.from(result.stdout ?? "");
  const stderr = Buffer.isBuffer(result.stderr) ? result.stderr.toString("utf8") : String(result.stderr ?? "");
  if (!(options.allowCodes ?? [0]).includes(status)) {
    throw new Error(`${command} ${argv.join(" ")} 失败（exit ${status}）${stderr ? `\n${stderr.trim()}` : ""}`);
  }
  return { status, stdout, stderr };
}

export function git(repo: string, argv: string[], options: { input?: Buffer | string; allowCodes?: number[] } = {}): Buffer {
  return run("git", ["-C", repo, ...argv], options).stdout;
}

export function gitText(repo: string, argv: string[], options: { input?: Buffer | string; allowCodes?: number[] } = {}): string {
  return git(repo, argv, options).toString("utf8");
}

export function gitStatus(repo: string, argv: string[]): number {
  return run("git", ["-C", repo, ...argv], { allowCodes: [0, 1] }).status;
}

export function realPathIfExists(path: string): string {
  return existsSync(path) ? realpathSync(path) : resolve(path);
}

export function gitRoot(path: string): string | undefined {
  const result = run("git", ["-C", path, "rev-parse", "--show-toplevel"], { allowCodes: [0, 128, 129] });
  if (result.status !== 0) return undefined;
  return resolve(result.stdout.toString("utf8").trim());
}

export function assertIndependentGitRoot(path: string): string {
  const root = gitRoot(path);
  if (!root) throw new Error(`不是 Git 仓库：${path}`);
  const target = resolve(path);
  if (root !== target) {
    throw new Error(`Git 根目录不是项目目录本身。检测到 Git 根目录：${root}\n本 Skill 不处理嵌套在其他 Git 仓库中的项目。`);
  }
  return root;
}

/**
 * Root commits have no parent. Use the empty tree as their virtual parent.
 * Compute the empty-tree ID with Git so SHA-1 and SHA-256 repositories work.
 * Reject merge commits: one teaching step must not span multiple parents.
 */
export function parentOrEmptyTree(repo: string, commit: string): string {
  const record = gitText(repo, ["rev-list", "--parents", "-n", "1", commit]).trim().split(/\s+/);
  if (record.length === 2) return record[1];
  if (record.length > 2) throw new Error(`不支持将 merge commit 当作单个教学步骤：${commit}`);
  return gitText(repo, ["hash-object", "-t", "tree", "--stdin"], { input: Buffer.alloc(0) }).trim();
}

export function gitHead(repo: string): string | undefined {
  const result = run("git", ["-C", repo, "rev-parse", "--verify", "HEAD"], { allowCodes: [0, 128] });
  return result.status === 0 ? result.stdout.toString("utf8").trim() : undefined;
}

export function isWithin(parent: string, child: string): boolean {
  const rel = relative(resolve(parent), resolve(child));
  return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel));
}

export function safeRelativePath(value: string): string {
  const normalized = value.replace(/\\/g, "/");
  if (!normalized || normalized.startsWith("/") || /^[A-Za-z]:/.test(normalized)) {
    throw new Error(`非法相对路径：${value}`);
  }
  const segments = normalized.split("/");
  if (segments.some((segment) => segment === ".." || segment === "")) {
    throw new Error(`非法相对路径：${value}`);
  }
  return normalized;
}

export function pathIn(root: string, rel: string): string {
  const clean = safeRelativePath(rel);
  const destination = resolve(root, ...clean.split("/"));
  if (!isWithin(root, destination) || resolve(root) === destination) {
    throw new Error(`路径越出根目录：${rel}`);
  }
  return destination;
}

export function ensureParent(path: string): void {
  mkdirSync(dirname(path), { recursive: true });
}

export function createSymlink(target: string, linkPath: string): void {
  ensureParent(linkPath);
  let type: "file" | "dir" | "junction" = process.platform === "win32" ? "junction" : "file";
  if (process.platform !== "win32") {
    try {
      if (statSync(target).isDirectory()) type = "dir";
    } catch {
      type = "file";
    }
  }
  symlinkSync(target, linkPath, type);
}

export function copyEntryNoFollow(source: string, destination: string): void {
  const info = lstatSync(source);
  ensureParent(destination);
  if (info.isSymbolicLink()) {
    const target = readlinkSync(source);
    let type: "file" | "dir" | "junction" = process.platform === "win32" ? "junction" : "file";
    try {
      if (statSync(source).isDirectory() && process.platform !== "win32") type = "dir";
    } catch {
      // Preserve dangling links as links; their target is not inspected further.
    }
    symlinkSync(target, destination, type);
    return;
  }
  if (info.isDirectory()) {
    mkdirSync(destination, { recursive: true, mode: info.mode });
    for (const entry of readdirSync(source)) {
      copyEntryNoFollow(join(source, entry), join(destination, entry));
    }
    return;
  }
  if (info.isFile()) {
    copyFileSync(source, destination);
    chmodSync(destination, info.mode & 0o777);
    return;
  }
  throw new Error(`不支持的文件类型：${source}`);
}

export function removePath(path: string): void {
  if (!existsSync(path)) {
    try {
      lstatSync(path);
    } catch {
      return;
    }
  }
  rmSync(path, { recursive: true, force: true });
}

export function parseNul(buffer: Buffer): string[] {
  return buffer.toString("utf8").split("\0").filter((item) => item.length > 0);
}

export function writeJson(path: string, value: unknown): void {
  ensureParent(path);
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

export function gitConfig(repo: string, key: string): string | undefined {
  const result = run("git", ["-C", repo, "config", "--get", key], { allowCodes: [0, 1] });
  return result.status === 0 ? result.stdout.toString("utf8").trim() : undefined;
}

export function assertGitIdentity(repo: string): void {
  const name = gitConfig(repo, "user.name");
  const email = gitConfig(repo, "user.email");
  if (!name || !email) {
    throw new Error(
      `Git 用户身份未配置，无法创建提交。请自行配置仓库级或全局 user.name / user.email；本 Skill 不会修改全局 Git 配置。`,
    );
  }
}

export function normalizeCommitPath(path: string): string {
  return safeRelativePath(path);
}

export function listUnstagedPaths(repo: string): string[] {
  const tracked = parseNul(git(repo, ["diff", "--name-only", "-z", "--no-ext-diff"]));
  const untracked = parseNul(git(repo, ["ls-files", "--others", "--exclude-standard", "-z"]));
  return [...new Set([...tracked, ...untracked])].sort();
}

export function getGitTreeEntry(repo: string, treeish: string, rel: string): { mode: string; type: string; hash: string } | undefined {
  const pathspec = `:(literal)${safeRelativePath(rel)}`;
  const output = git(repo, ["ls-tree", "-z", treeish, "--", pathspec]);
  const record = parseNul(output)[0];
  if (!record) return undefined;
  const tab = record.indexOf("\t");
  if (tab < 0) throw new Error(`无法解析 git ls-tree 输出：${record}`);
  const [mode, type, hash] = record.slice(0, tab).split(" ");
  return { mode, type, hash };
}

export function getIndexEntry(repo: string, rel: string): { mode: string; hash: string; stage: string } | undefined {
  const pathspec = `:(literal)${safeRelativePath(rel)}`;
  const records = parseNul(git(repo, ["ls-files", "--stage", "-z", "--", pathspec]));
  if (records.length === 0) return undefined;
  if (records.length !== 1) throw new Error(`暂存区存在冲突条目：${rel}`);
  const tab = records[0].indexOf("\t");
  const [mode, hash, stage] = records[0].slice(0, tab).split(" ");
  return { mode, hash, stage };
}

export interface FileState {
  exists: boolean;
  mode?: string;
  hash?: string;
  type?: "file" | "symlink";
  modeComparable?: boolean;
}

export function hashBuffer(repo: string, data: Buffer): string {
  return gitText(repo, ["hash-object", "--stdin"], { input: data }).trim();
}

export function getWorktreeState(repo: string, rel: string): FileState {
  const full = pathIn(repo, rel);
  let info;
  try {
    info = lstatSync(full);
  } catch {
    return { exists: false };
  }
  if (info.isSymbolicLink()) {
    const data = Buffer.from(readlinkSync(full), "utf8");
    return { exists: true, mode: "120000", hash: hashBuffer(repo, data), type: "symlink" };
  }
  if (!info.isFile()) throw new Error(`当前步骤路径不是普通文件或符号链接：${rel}`);
  const data = readFileSync(full);
  const filemode = gitConfig(repo, "core.filemode");
  const executable = process.platform !== "win32" && filemode !== "false" && (info.mode & 0o111) !== 0;
  return { exists: true, mode: executable ? "100755" : "100644", hash: hashBuffer(repo, data), type: "file", modeComparable: process.platform !== "win32" && filemode !== "false" };
}

export function statesEqual(a: FileState | undefined, b: FileState | undefined): boolean {
  const left = a ?? { exists: false };
  const right = b ?? { exists: false };
  if (left.exists !== right.exists) return false;
  if (!left.exists && !right.exists) return true;
  if (left.hash !== right.hash) return false;
  if (left.modeComparable === false || right.modeComparable === false) return left.type === right.type;
  return left.mode === right.mode;
}

export function indexState(entry: { mode: string; hash: string } | undefined): FileState {
  return entry ? { exists: true, mode: entry.mode, hash: entry.hash, type: entry.mode === "120000" ? "symlink" : "file" } : { exists: false };
}

export function treeState(entry: { mode: string; type: string; hash: string } | undefined): FileState {
  return entry ? { exists: true, mode: entry.mode, hash: entry.hash, type: entry.mode === "120000" ? "symlink" : "file" } : { exists: false };
}

export function gitWriteTree(repo: string): string {
  return gitText(repo, ["write-tree"]).trim();
}

export function ensureNoPathCollision(path: string): void {
  try {
    lstatSync(path);
    throw new Error(`目标位置已存在，拒绝覆盖：${path}`);
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("目标位置已存在")) throw error;
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

export const CORE_TEACHING_DOCS = [
  "docs/development-plan.md",
  "docs/development-log.md",
  "docs/teaching-state.md",
  "docs/NOTES.md",
  "docs/MISSION.md",
  "docs/RESOURCES.md",
] as const;

export function commitCodePaths(repo: string, commit: string): string[] {
  const paths = parseNul(git(repo, ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", commit]));
  return paths.filter((path) => !(CORE_TEACHING_DOCS as readonly string[]).includes(path));
}

/** Locate the next teaching commit from the durable local queue.
 * Returns undefined when no batch is active or paused (legacy projects, fresh projects, or finished batches).
 */
export function getTeachingQueueCursor(agent: string): { stepId: string; commit: string; status: string; paths: string[] } | undefined {
  const file = join(agent, "docs", "teaching-state.md");
  if (!existsSync(file)) return undefined;
  const content = readFileSync(file, "utf8");
  const beginMarker = "<!-- teaching-state-index:start -->";
  const endMarker = "<!-- teaching-state-index:end -->";
  const begin = content.indexOf(beginMarker);
  if (begin < 0) return undefined;
  const end = content.indexOf(endMarker, begin + beginMarker.length);
  if (end < 0) throw new Error("teaching-state.md 状态索引损坏（缺少结束标记）");
  const state = JSON.parse(content.slice(begin + beginMarker.length, end).trim()) as {
    version?: number;
    batch?: { status: string; nextIndex: number; steps: Array<{ id: string; commit: string; paths?: string[] }> } | null;
  };
  if (state.version !== 2) throw new Error("不支持的 teaching-state.md 状态索引版本");
  if (!state.batch || state.batch.status === "finished") return undefined;
  const next = state.batch.steps[state.batch.nextIndex];
  if (!next) throw new Error("教学队列当前游标不存在；停止自动操作并核对本地状态");
  const paths = Array.isArray(next.paths) ? [...new Set(next.paths.map((p) => safeRelativePath(p)))].sort() : [];
  return { stepId: next.id, commit: next.commit, status: state.batch.status, paths };
}
