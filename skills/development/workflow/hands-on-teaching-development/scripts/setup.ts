#!/usr/bin/env tsx
/**
 * Workspace and project initialization.
 * Run in phases because the AI may need to create a minimal User Project skeleton
 * between `workspace` and `user-baseline`.
 */
import {
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  renameSync,
  statSync,
} from "node:fs";
import { dirname, basename, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  arg,
  argsAll,
  assertGitIdentity,
  createSymlink,
  ensureNoPathCollision,
  git,
  gitRoot,
  gitText,
  gitWriteTree,
  gitHead,
  isWithin,
  parseCli,
} from "./common.ts";
import { copyProject } from "./copy-project.ts";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const SKILL_DIR = dirname(SCRIPT_DIR);

function pathExistsIncludingDangling(path: string): boolean {
  try {
    lstatSync(path);
    return true;
  } catch {
    return false;
  }
}

function parseExample(value: string): { name: string; path: string } {
  const split = value.indexOf("=");
  if (split > 0 && !isAbsolute(value) && !value.startsWith("./") && !value.startsWith("../")) {
    return { name: value.slice(0, split), path: value.slice(split + 1) };
  }
  const absolute = resolve(value);
  return { name: basename(absolute), path: value };
}

function copyWorkspaceAgents(workspace: string): void {
  const source = join(SKILL_DIR, "references", "AGENTS.md");
  const destination = join(workspace, "AGENTS.md");
  if (!existsSync(source)) throw new Error(`Skill 参考约束文件不存在：${source}`);
  if (pathExistsIncludingDangling(destination)) {
    let same = false;
    try {
      same = existsSync(destination) && lstatSync(destination).isFile() && readFileSync(source).equals(readFileSync(destination));
    } catch {
      same = false;
    }
    if (same) console.log("workspace/AGENTS.md 已存在且内容一致，跳过复制。");
    else console.warn("workspace/AGENTS.md 已存在且内容不同或不是普通文件，未覆盖。请先阅读现有文件，再由用户决定是否合并教学约束。");
    return;
  }
  copyFileSync(source, destination);
  console.log(`已复制工作区约束：${destination}`);
}

function addExample(workspace: string, examplesDir: string, value: string): void {
  const parsed = parseExample(value);
  if (!parsed.name || parsed.name === "." || parsed.name === ".." || parsed.name.includes("/") || parsed.name.includes("\\")) {
    throw new Error(`示例项目名称必须是单层目录名：${parsed.name}`);
  }
  const source = resolve(parsed.path);
  if (!pathExistsIncludingDangling(source)) throw new Error(`示例项目路径不存在：${source}`);
  const sourceInfo = lstatSync(source);
  if (!sourceInfo.isDirectory() && !sourceInfo.isSymbolicLink()) {
    throw new Error(`示例项目必须是目录或目录符号链接：${source}`);
  }
  if (!statSync(source).isDirectory()) throw new Error(`示例项目链接目标不是可访问目录：${source}`);

  if (resolve(source) === resolve(workspace) || resolve(source) === resolve(examplesDir)) {
    throw new Error(`不能将 workspace 根目录或 examples/ 本身作为单个 Example Project：${source}`);
  }

  // A project already inside examples/ stays where it is.
  if (isWithin(examplesDir, source)) {
    console.log(`示例项目已位于 examples/，保持不动：${source}`);
    return;
  }

  const destination = join(examplesDir, parsed.name);
  ensureNoPathCollision(destination);
  if (isWithin(workspace, source)) {
    // A workspace-local project is moved, preserving its own .git history.
    renameSync(source, destination);
    console.log(`已将工作区内示例项目移动到：${destination}`);
  } else {
    // External examples are linked. The target is expected to exist.
    createSymlink(source, destination);
    console.log(`已创建示例项目符号链接：${destination} -> ${source}`);
  }
}

function workspaceCommand(args: ReturnType<typeof parseCli>): void {
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const userTargetArg = arg(args, "user-project");
  if (!userTargetArg) throw new Error("workspace 初始化必须提供 --user-project <绝对或相对路径>");
  mkdirSync(workspace, { recursive: true });
  mkdirSync(join(workspace, ".teaching", "review"), { recursive: true });
  mkdirSync(join(workspace, ".teaching", "backup"), { recursive: true });
  const examplesDir = join(workspace, "examples");
  if (pathExistsIncludingDangling(examplesDir)) {
    if (!lstatSync(examplesDir).isDirectory()) throw new Error(`examples/ 必须是普通目录：${examplesDir}`);
  } else {
    mkdirSync(examplesDir, { recursive: true });
  }
  copyWorkspaceAgents(workspace);

  const examples = argsAll(args, "example");
  if (examples.length === 0) console.warn("未提供 --example；请确认是否已有 examples/ 内容，或稍后重新运行 workspace 命令添加示例项目。");
  for (const example of examples) addExample(workspace, examplesDir, example);

  const userTarget = resolve(userTargetArg);
  const userLink = join(workspace, "user-project");
  if (resolve(userTarget) === resolve(userLink)) throw new Error("User Project 目标不能指向 workspace/user-project 链接本身。");
  const effectiveUserTarget = existsSync(userTarget) ? realpathSync(userTarget) : resolve(userTarget);
  for (const entry of readdirSync(examplesDir)) {
    const examplePath = join(examplesDir, entry);
    if (!pathExistsIncludingDangling(examplePath)) continue;
    // A dangling link is visible to lstatSync but not usable by statSync.
    if (!existsSync(examplePath)) {
      console.warn(`跳过不可访问的示例项目（可能是悬空符号链接）：${examplePath}`);
      continue;
    }
    // A concurrently removed/retargeted link must not abort setup with an opaque ENOENT.
    let effectiveExample: string;
    try {
      if (!statSync(examplePath).isDirectory()) continue;
      effectiveExample = realpathSync(examplePath);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        console.warn(`跳过已失效的示例项目路径：${examplePath}`);
        continue;
      }
      throw error;
    }
    if (isWithin(effectiveExample, effectiveUserTarget) || isWithin(effectiveUserTarget, effectiveExample)) {
      throw new Error(`User Project 与 Example Project 路径重叠，可能破坏示例项目只读边界：\nExample: ${effectiveExample}\nUser: ${effectiveUserTarget}`);
    }
  }
  if (pathExistsIncludingDangling(userLink)) {
    const info = lstatSync(userLink);
    if (!info.isSymbolicLink()) throw new Error(`workspace/user-project 已存在但不是符号链接，拒绝覆盖：${userLink}`);
    const currentTarget = resolve(dirname(userLink), readlinkSync(userLink));
    if (currentTarget !== userTarget) throw new Error(`workspace/user-project 已指向其他位置：${currentTarget}\n不会自动替换现有链接。`);
    console.log(`User Project 符号链接已存在：${userLink} -> ${userTarget}`);
  } else {
    // Create the link unconditionally before ensuring its target directory exists.
    createSymlink(userTarget, userLink);
    console.log(`已创建 User Project 符号链接：${userLink} -> ${userTarget}`);
  }
  mkdirSync(userTarget, { recursive: true });

  const names = readdirSafe(userTarget);
  console.log(`User Project 目标目录：${userTarget}`);
  console.log(names.length === 0 ? "检测结果：空目录（按不存在项目处理，等待 AI 初始化骨架）。" : `检测结果：已有 ${names.length} 个顶层条目。`);
  console.log("workspace 阶段完成。下一步由 AI 初始化或检查 User Project，再运行 user-baseline。");
}

function readdirSafe(path: string): string[] {
  try {
    return readdirSync(path);
  } catch {
    return [];
  }
}

function resolveUserProject(workspace: string): string {
  const link = join(workspace, "user-project");
  if (!pathExistsIncludingDangling(link) || !lstatSync(link).isSymbolicLink()) {
    throw new Error(`未找到 User Project 符号链接：${link}。先运行 setup.ts workspace。`);
  }
  return realpathSync(link);
}

function userBaselineCommand(args: ReturnType<typeof parseCli>): void {
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const userProject = resolveUserProject(workspace);
  mkdirSync(userProject, { recursive: true });
  let root = gitRoot(userProject);
  if (!root) {
    git(userProject, ["init"]);
    console.log("User Project 尚无 Git 仓库，已执行 git init（未创建 Commit）。");
    root = gitRoot(userProject);
  }
  if (!root || resolve(root) !== resolve(userProject)) {
    throw new Error(`User Project 必须是独立 Git 仓库。检测到 Git 根目录：${root ?? "无"}`);
  }
  git(userProject, ["add", "-A"]);
  console.log(`已执行 git add -A，建立 User Project 教学基线。Index Tree：${gitWriteTree(userProject)}`);
  console.log("未创建 User Project Commit；原有历史、分支和仓库配置均予以保留。");
}

function ensureTemplate(source: string, destination: string): void {
  if (pathExistsIncludingDangling(destination)) {
    try {
      if (statSync(destination).isFile()) return;
    } catch {
      // A dangling symlink is not a usable template destination.
    }
    throw new Error(`Agent Project 文档目标已存在但不是可用文件，拒绝覆盖：${destination}`);
  }
  mkdirSync(dirname(destination), { recursive: true });
  copyFileSync(source, destination);
}

function agentInitCommand(args: ReturnType<typeof parseCli>): void {
  const workspace = resolve(arg(args, "workspace") ?? process.cwd());
  const name = arg(args, "agent-name");
  if (!name) throw new Error("必须提供用户确认过的 --agent-name");
  if (name === "examples" || name === "user-project" || name === ".teaching" || name.includes("/") || name.includes("\\") || name === "." || name === "..") {
    throw new Error(`Agent Project 名称必须是独立的单层目录名，且不能占用保留名称：${name}`);
  }
  const userProject = resolveUserProject(workspace);
  const userRoot = gitRoot(userProject);
  if (!userRoot || resolve(userRoot) !== resolve(userProject)) throw new Error("请先运行 user-baseline，确保 User Project 已建立独立 Git 基线。");

  const agentProject = join(workspace, name);
  if (!pathExistsIncludingDangling(agentProject)) {
    const stats = copyProject(userProject, agentProject);
    console.log(`已从 User Project 创建 Agent Project：${agentProject}`);
    console.log(`复制统计：${stats.files} 文件、${stats.directories} 目录、${stats.symlinks} 符号链接、忽略 ${stats.ignored} 项。`);
  } else if (!gitRoot(agentProject)) {
    throw new Error(`Agent Project 目标已存在但不是 Git 仓库，拒绝覆盖：${agentProject}`);
  }

  const templatesDir = join(SKILL_DIR, "templates");
  ensureTemplate(join(templatesDir, "development-plan.md"), join(agentProject, "docs", "development-plan.md"));
  ensureTemplate(join(templatesDir, "development-log.md"), join(agentProject, "docs", "development-log.md"));
  ensureTemplate(join(templatesDir, "teaching-state.md"), join(agentProject, "docs", "teaching-state.md"));

  const agentRoot = gitRoot(agentProject);
  if (agentRoot && resolve(agentRoot) !== resolve(agentProject)) throw new Error(`Agent Project 不是独立 Git 仓库：${agentRoot}`);
  if (!agentRoot) git(agentProject, ["init"]);
  if (!gitHead(agentProject)) {
    git(agentProject, ["add", "-A"]);
    for (const doc of ["docs/development-plan.md", "docs/development-log.md", "docs/teaching-state.md"]) {
      git(agentProject, ["add", "-f", "--", doc]);
    }
    assertGitIdentity(agentProject);
    git(agentProject, ["commit", "-m", "[agent] initial(init): 初始化项目基础结构"]);
    console.log(`已创建 Agent Project 初始 Commit：${gitText(agentProject, ["rev-parse", "--short", "HEAD"]).trim()}`);
  } else {
    console.log(`Agent Project 已有 Git 历史，保留现有提交：${gitText(agentProject, ["rev-parse", "--short", "HEAD"]).trim()}`);
  }
  console.log(`Agent Project 初始化完成：${agentProject}`);
}

function printHelp(): void {
  console.log(`hands-on-teaching-development setup

分阶段用法：

1. 创建 workspace 结构、导入示例项目、创建 User Project 链接并复制 AGENTS.md：
   npx tsx setup.ts workspace --workspace . --example /path/to/example-a --example name=/path/to/example-b --user-project /path/to/user-project

2. AI 检查或初始化 User Project 后，建立 Git 基线：
   npx tsx setup.ts user-baseline --workspace .

3. 用户确认 Agent Project 名称后，创建独立 Agent Project 和 initial Commit：
   npx tsx setup.ts agent-init --workspace . --agent-name my-product

规则：
- workspace/examples 下可同时存在实体目录和符号链接。
- workspace 内的示例项目移动到 examples；外部示例项目创建符号链接。
- User Project 始终通过 workspace/user-project 符号链接访问。
- user-baseline 执行 git add -A，不创建 User Project Commit。
- Agent Project 从 User Project 复制当前文件，不复制 .git，并独立初始化 Git。
- 不覆盖已存在的 AGENTS.md、链接或 Agent Project。`);
}

function main(): void {
  const args = parseCli(process.argv.slice(2));
  const command = args.positionals[0];
  if (!command || command === "--help" || args.values.has("help")) return printHelp();
  if (command === "workspace") workspaceCommand(args);
  else if (command === "user-baseline") userBaselineCommand(args);
  else if (command === "agent-init") agentInitCommand(args);
  else throw new Error(`未知 setup 命令：${command}`);
}

const direct = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (direct) main();
