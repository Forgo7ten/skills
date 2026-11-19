#!/usr/bin/env tsx
import { fileURLToPath } from "node:url";
/**
 * Copy a User Project into a new Agent Project tree.
 * - Never copies .git entries.
 * - Uses git check-ignore --no-index, so .gitignore rules apply even to tracked files.
 * - Preserves internal symlinks and never follows them.
 */
import {
  chmodSync,
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readlinkSync,
  readdirSync,
  realpathSync,
  statSync,
  symlinkSync,
} from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { arg, ensureNoPathCollision, gitRoot, parseCli, run } from "./common.ts";

export interface CopyStats {
  files: number;
  directories: number;
  symlinks: number;
  ignored: number;
  gitMetadata: number;
}

function normalizeGitPath(value: string): string {
  return value.replace(/\\/g, "/").replace(/\/$/, "");
}

function ignoredPaths(sourceRoot: string, candidates: string[]): Set<string> {
  if (candidates.length === 0) return new Set();
  const input = Buffer.from(`${candidates.map((item) => item.replace(/\\/g, "/")).join("\0")}\0`, "utf8");
  const result = run("git", ["-C", sourceRoot, "check-ignore", "--no-index", "-z", "--stdin"], {
    input,
    allowCodes: [0, 1],
  });
  return new Set(result.stdout.toString("utf8").split("\0").filter(Boolean).map(normalizeGitPath));
}

function copySymlink(source: string, destination: string): void {
  const target = readlinkSync(source);
  let type: "file" | "dir" | "junction" = process.platform === "win32" ? "junction" : "file";
  try {
    if (statSync(source).isDirectory() && process.platform !== "win32") type = "dir";
  } catch {
    // Dangling links are copied as links. The target is deliberately not inspected.
  }
  mkdirSync(dirname(destination), { recursive: true });
  symlinkSync(target, destination, type);
}

export function copyProject(sourcePath: string, destinationPath: string): CopyStats {
  const sourceRoot = realpathSync(sourcePath);
  const root = gitRoot(sourceRoot);
  if (!root || resolve(root) !== resolve(sourceRoot)) {
    throw new Error(`复制来源必须是独立 Git 仓库：${sourceRoot}`);
  }
  const destination = resolve(destinationPath);
  if (destination === sourceRoot || destination.startsWith(`${sourceRoot}${sep}`)) {
    throw new Error("Agent Project 目标目录不能位于 User Project 内部");
  }
  ensureNoPathCollision(destination);
  mkdirSync(destination, { recursive: true });

  const stats: CopyStats = { files: 0, directories: 0, symlinks: 0, ignored: 0, gitMetadata: 0 };

  const walk = (sourceDir: string, destinationDir: string, relativeBase: string): void => {
    const entries = readdirSync(sourceDir, { withFileTypes: true });
    const candidates: string[] = [];
    for (const entry of entries) {
      if (entry.name === ".git") {
        stats.gitMetadata += 1;
        continue;
      }
      const rel = relativeBase ? `${relativeBase}/${entry.name}` : entry.name;
      const sourceEntry = join(sourceDir, entry.name);
      const info = lstatSync(sourceEntry);
      const isRealDirectory = info.isDirectory();
      candidates.push(isRealDirectory ? `${rel}/` : rel);
    }
    const ignored = ignoredPaths(sourceRoot, candidates);

    for (const entry of entries) {
      if (entry.name === ".git") continue;
      const rel = relativeBase ? `${relativeBase}/${entry.name}` : entry.name;
      const sourceEntry = join(sourceDir, entry.name);
      const destinationEntry = join(destinationDir, entry.name);
      const info = lstatSync(sourceEntry);
      const ignoreKey = normalizeGitPath(info.isDirectory() ? `${rel}/` : rel);
      if (ignored.has(ignoreKey) || ignored.has(normalizeGitPath(rel))) {
        stats.ignored += 1;
        continue;
      }

      if (info.isSymbolicLink()) {
        copySymlink(sourceEntry, destinationEntry);
        stats.symlinks += 1;
      } else if (info.isDirectory()) {
        mkdirSync(destinationEntry, { recursive: true, mode: info.mode });
        stats.directories += 1;
        walk(sourceEntry, destinationEntry, rel);
      } else if (info.isFile()) {
        mkdirSync(dirname(destinationEntry), { recursive: true });
        copyFileSync(sourceEntry, destinationEntry);
        chmodSync(destinationEntry, info.mode & 0o777);
        stats.files += 1;
      }
    }
  };

  walk(sourceRoot, destination, "");
  return stats;
}

function printHelp(): void {
  console.log(`copy-project.ts

用法:
  npx tsx copy-project.ts --source <User Project> --destination <Agent Project>

规则:
  - 排除任意层级的 .git 文件/目录
  - 使用 git check-ignore --no-index 应用 Git ignore 规则（包括已跟踪但匹配忽略规则的文件）
  - 保留项目内部符号链接，不跟随链接目标
  - 目标目录必须不存在，不覆盖已有内容`);
}

function main(): void {
  const args = parseCli(process.argv.slice(2));
  if (args.positionals.includes("--help") || arg(args, "help") === "true") {
    printHelp();
    return;
  }
  const source = arg(args, "source");
  const destination = arg(args, "destination");
  if (!source || !destination) throw new Error("需要 --source 和 --destination");
  const stats = copyProject(source, destination);
  console.log(`复制完成：${stats.files} 个文件，${stats.directories} 个目录，${stats.symlinks} 个符号链接，忽略 ${stats.ignored} 项，排除 ${stats.gitMetadata} 个 .git 项。`);
}

const direct = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (direct) main();
