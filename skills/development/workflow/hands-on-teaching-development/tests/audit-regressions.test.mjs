import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, symlinkSync, readlinkSync, realpathSync, existsSync, rmSync, renameSync } from "node:fs";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { realPathIfExists, gitWriteTree } from "../scripts/common.ts";
import { clearWorktreeWithBackup } from "../scripts/backup-worktree.ts";
import { rollbackFailedAgentApply } from "../scripts/sync-user.ts";

const skillDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function resolveNodeFlags() {
  if (process.env.TEACHING_NATIVE_TS === "1") return ["--experimental-strip-types"];
  try {
    createRequire(import.meta.url).resolve("tsx");
    return ["--import", "tsx"];
  } catch {
    return ["--experimental-strip-types"];
  }
}

const nodeFlags = resolveNodeFlags();

function call(command, args, { cwd, input, expected = 0 } = {}) {
  const r = spawnSync(command, args, { cwd, input, encoding: "utf8", windowsHide: true, env: { ...process.env, NODE_NO_WARNINGS: "1" } });
  if (r.error) throw r.error;
  assert.equal(r.status, expected, `${command} ${args.join(" ")} => ${r.status}\nSTDOUT: ${r.stdout}\nSTDERR: ${r.stderr}`);
  return r.stdout;
}
function git(dir, ...args) { return call("git", ["-C", dir, ...args]); }
function script(name, ...args) { return call(process.execPath, [...nodeFlags, join(skillDir, "scripts", name), ...args]); }
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "teaching-audit-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}
function initRepo(dir) {
  mkdirSync(dir, { recursive: true });
  call("git", ["init", "-q", dir]);
  git(dir, "config", "user.name", "Skill Test");
  git(dir, "config", "user.email", "test@example.invalid");
}
function put(path, content) { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, content); }
function commit(dir, message) { git(dir, "add", "-A"); git(dir, "commit", "-q", "-m", message); return git(dir, "rev-parse", "HEAD").trim(); }
function pathsFile(root, paths) { const file = join(root, "paths.json"); put(file, JSON.stringify(paths)); return file; }

// Regression 1: existing symlinks are resolved, absent paths preserve their names.
test("realPathIfExists uses the physical path only when the target exists", (t) => {
  const root = fixture(t);
  const actual = join(root, "physical");
  const link = join(root, "alias");
  mkdirSync(actual); symlinkSync(actual, link, "dir");
  assert.equal(realPathIfExists(link), realpathSync(actual));
  assert.equal(realPathIfExists(join(root, "not-created")), resolve(root, "not-created"));
});

// Regression 2: an existing dangling example symlink must not cause statSync ENOENT.
test("workspace setup tolerates dangling example links without an uncaught ENOENT", (t) => {
  const root = fixture(t);
  const workspace = join(root, "workspace");
  const examples = join(workspace, "examples");
  mkdirSync(examples, { recursive: true });
  symlinkSync(join(root, "deleted-source"), join(examples, "dangling"), "dir");
  const stdout = script("setup.ts", "workspace", "--workspace", workspace, "--user-project", join(root, "user"));
  assert.match(stdout, /workspace 阶段完成/);
  assert.ok(existsSync(join(workspace, "user-project")));
  assert.ok(existsSync(join(workspace, "AGENTS.md")), "references/AGENTS.md must be copied on setup");
});

// Regression 3 + 4: root commits must produce paths and new files must yield a patch.
test("root-commit review identifies new files and emits applicable text/binary/symlink patch", (t) => {
  const root = fixture(t);
  const user = join(root, "user"); const agent = join(root, "agent"); const workspace = join(root, "workspace");
  initRepo(user); initRepo(agent); mkdirSync(workspace);
  put(join(agent, "src", "hello.txt"), "hello\n");
  put(join(agent, "src", "binary.bin"), Buffer.from([0, 1, 2, 255, 0]));
  symlinkSync("hello.txt", join(agent, "src", "short-link"));
  const rootCommit = commit(agent, "[agent] feat(core): root feature");
  put(join(user, "src", "hello.txt"), "hello\n");
  put(join(user, "src", "binary.bin"), Buffer.from([0, 1, 2, 255, 0]));
  symlinkSync("hello.txt", join(user, "src", "short-link"));
  const out = JSON.parse(script("review-diff.ts", "review", "--user-dir", user, "--agent-dir", agent, "--workspace", workspace));
  assert.equal(out.status, "EXACT_MATCH");
  assert.equal(out.agentCommit, rootCommit);
  assert.deepEqual(out.paths, ["src/binary.bin", "src/hello.txt", "src/short-link"]);
  const patch = readFileSync(out.reports.userPatch, "utf8");
  assert.match(patch, /src\/hello\.txt/);
  assert.match(patch, /GIT binary patch/);
  assert.match(patch, /new file mode 120000/);
  const emptyRepo = join(root, "apply-dest"); initRepo(emptyRepo);
  call("git", ["-C", emptyRepo, "apply", "--check", "--binary", "-"], { input: patch });
  call("git", ["-C", emptyRepo, "apply", "--binary", "-"], { input: patch });
  assert.equal(readFileSync(join(emptyRepo, "src", "hello.txt"), "utf8"), "hello\n");
  assert.deepEqual(readFileSync(join(emptyRepo, "src", "binary.bin")), Buffer.from([0, 1, 2, 255, 0]));
  assert.equal(readlinkSync(join(emptyRepo, "src", "short-link")), "hello.txt");
});

test("extra untracked files appear in user-extra.patch", (t) => {
  const root = fixture(t);
  const user = join(root, "user"); const agent = join(root, "agent"); const workspace = join(root, "workspace");
  initRepo(user); initRepo(agent); mkdirSync(workspace);
  put(join(agent, "src", "a.txt"), "a\n"); commit(agent, "[agent] feat(core): init");
  put(join(user, "src", "a.txt"), "a\n"); put(join(user, "notes.txt"), "unrelated\n");
  const out = JSON.parse(script("review-diff.ts", "review", "--user-dir", user, "--agent-dir", agent, "--workspace", workspace));
  assert.equal(out.status, "DIFFERENT");
  assert.deepEqual(out.extraUserPaths, ["notes.txt"]);
  assert.match(readFileSync(out.reports.userExtraPatch, "utf8"), /notes\.txt/);
});

// Regression 5: preserve previous steps staged in the index while recovering dirty files.
test("failure rollback restores full staged baseline and previous dirty work", (t) => {
  const root = fixture(t);
  const user = join(root, "user"); const workspace = join(root, "workspace");
  initRepo(user); mkdirSync(workspace);
  put(join(user, "src", "feature.txt"), "baseline\n");
  put(join(user, "src", "previous-step.txt"), "user-approved-staged\n");
  git(user, "add", "-A");
  const before = gitWriteTree(user);
  put(join(user, "src", "feature.txt"), "half-written-code\n");
  put(join(user, "scratch", "notes.txt"), "unfinished note\n");
  const backup = clearWorktreeWithBackup(user, workspace);
  assert.equal(backup.backedUp, true);
  // Simulate successful git apply --index followed by a failed verification.
  put(join(user, "src", "feature.txt"), "agent-patch\n");
  put(join(user, "src", "added.txt"), "created by agent patch\n");
  git(user, "add", "src/feature.txt", "src/added.txt");
  assert.notEqual(gitWriteTree(user), before);
  rollbackFailedAgentApply(user, workspace, before, ["src/feature.txt", "src/added.txt"], true);
  assert.equal(gitWriteTree(user), before, "all staged historical steps must survive unchanged");
  assert.equal(readFileSync(join(user, "src", "feature.txt"), "utf8"), "half-written-code\n");
  assert.equal(readFileSync(join(user, "scratch", "notes.txt"), "utf8"), "unfinished note\n");
  assert.equal(existsSync(join(user, "src", "added.txt")), false);
  assert.equal(readFileSync(join(user, "src", "previous-step.txt"), "utf8"), "user-approved-staged\n");
});

test("root Agent commit can be directly applied to a fresh User index", (t) => {
  const root = fixture(t);
  const user = join(root, "user"); const agent = join(root, "agent"); const workspace = join(root, "workspace");
  initRepo(user); initRepo(agent); mkdirSync(workspace);
  put(join(agent, "src", "root.txt"), "root feature\n");
  const rootCommit = commit(agent, "[agent] feat(core): root action");
  const paths = pathsFile(root, ["src/root.txt"]);
  const result = JSON.parse(script("sync-user.ts", "apply-agent", "--user-dir", user, "--agent-dir", agent, "--workspace", workspace, "--agent-commit", rootCommit, "--paths-file", paths));
  assert.equal(result.status, "APPLIED");
  assert.equal(readFileSync(join(user, "src", "root.txt"), "utf8"), "root feature\n");
  assert.match(git(user, "diff", "--cached", "--stat"), /root.txt/);
});

test("checkpoint refuses to split modified teaching docs from code", (t) => {
  const root = fixture(t);
  const agent = join(root, "agent"); initRepo(agent);
  put(join(agent, "src", "a.txt"), "base\n");
  put(join(agent, "docs", "development-log.md"), "original\n");
  commit(agent, "[agent] initial(init): initial");
  put(join(agent, "src", "a.txt"), "changed\n");
  put(join(agent, "docs", "development-log.md"), "step record\n");
  const file = pathsFile(root, ["src/a.txt"]);
  const fail = spawnSync(process.execPath, [...nodeFlags, join(skillDir, "scripts", "checkpoint.ts"), "--agent-dir", agent, "--source", "agent", "--type", "feat", "--scope", "core", "--message", "step", "--paths-file", file], { encoding: "utf8", env: { ...process.env, NODE_NO_WARNINGS: "1" } });
  assert.notEqual(fail.status, 0);
  assert.match(fail.stderr, /教学文档/);
  put(file, JSON.stringify(["src/a.txt", "docs/development-log.md"]));
  const ok = JSON.parse(script("checkpoint.ts", "--agent-dir", agent, "--source", "agent", "--type", "feat", "--scope", "core", "--message", "step", "--paths-file", file));
  assert.deepEqual(ok.files, ["docs/development-log.md", "src/a.txt"]);
});

test("copy-project filters nested gitignore rules and preserves symlinks", (t) => {
  const root = fixture(t);
  const user = join(root, "user"); const agent = join(root, "agent");
  initRepo(user);
  put(join(user, ".gitignore"), "build/\n!src/keep.txt\n");
  put(join(user, "build", "out.o"), "compiled\n");
  put(join(user, "src", ".gitignore"), "*.tmp\n");
  put(join(user, "src", "temp.tmp"), "ignore\n");
  put(join(user, "src", "keep.txt"), "keep\n");
  symlinkSync("keep.txt", join(user, "src", "shortcut"));
  git(user, "add", "-A");
  const result = script("copy-project.ts", "--source", user, "--destination", agent);
  assert.match(result, /复制完成/);
  assert.ok(existsSync(join(agent, "src", "keep.txt")));
  assert.ok(!existsSync(join(agent, "build", "out.o")));
  assert.ok(!existsSync(join(agent, "src", "temp.tmp")));
  assert.equal(readlinkSync(join(agent, "src", "shortcut")), "keep.txt");
  assert.equal(existsSync(join(agent, ".git")), false);
});

test("apply-agent with a half-written file backs up dirt and preserves historical staged work", (t) => {
  const root = fixture(t);
  const user = join(root, "user"); const agent = join(root, "agent"); const workspace = join(root, "workspace");
  initRepo(user); initRepo(agent); mkdirSync(workspace);
  put(join(agent, "src", "current.txt"), "baseline\n");
  put(join(agent, "src", "previous.txt"), "already staged\n");
  commit(agent, "[agent] initial(init): base");
  put(join(agent, "src", "current.txt"), "agent approved\n");
  const step = commit(agent, "[agent] feat(core): next");
  put(join(user, "src", "current.txt"), "baseline\n");
  put(join(user, "src", "previous.txt"), "already staged\n");
  git(user, "add", "-A");
  put(join(user, "src", "current.txt"), "half-written\n");
  put(join(user, "scratch.txt"), "unfinished\n");
  const file = pathsFile(root, ["src/current.txt"]);
  const result = JSON.parse(script("sync-user.ts", "apply-agent", "--user-dir", user, "--agent-dir", agent, "--workspace", workspace, "--agent-commit", step, "--paths-file", file));
  assert.equal(result.status, "APPLIED");
  assert.ok(existsSync(join(workspace, ".teaching", "backup", "current", "manifest.json")));
  assert.equal(readFileSync(join(user, "src", "current.txt"), "utf8"), "agent approved\n");
  assert.equal(readFileSync(join(user, "src", "previous.txt"), "utf8"), "already staged\n");
  assert.equal(existsSync(join(user, "scratch.txt")), false, "the unfinished file belongs to the backup");
  assert.match(git(user, "diff", "--cached", "--name-only"), /current.txt/);
});

test("end-to-end: review worktree, verify staged, absorb, document user checkpoint", (t) => {
  const root = fixture(t);
  const user = join(root, "user"); const agent = join(root, "agent"); const workspace = join(root, "workspace");
  initRepo(user); initRepo(agent); mkdirSync(workspace);
  put(join(agent, "config.txt"), "baseline\n");
  put(join(agent, "docs", "development-log.md"), "# Log\n");
  commit(agent, "[agent] initial(init): base");
  put(join(agent, "config.txt"), "agent implementation\n");
  commit(agent, "[agent] feat(config): implement parser");
  put(join(user, "config.txt"), "baseline\n"); git(user, "add", "-A");
  put(join(user, "config.txt"), "agent implementation\n");
  const review = JSON.parse(script("review-diff.ts", "review", "--user-dir", user, "--agent-dir", agent, "--workspace", workspace));
  assert.equal(review.status, "EXACT_MATCH");
  git(user, "add", "--", "config.txt");
  const verification = JSON.parse(script("review-diff.ts", "verify-staged", "--user-dir", user, "--workspace", workspace));
  assert.equal(verification.status, "VERIFIED");
  const absorption = JSON.parse(script("sync-user.ts", "absorb", "--user-dir", user, "--agent-dir", agent, "--workspace", workspace));
  assert.equal(absorption.status, "ABSORBED");
  put(join(agent, "docs", "development-log.md"), "# Log\nUser completed step\n");
  const file = pathsFile(root, ["docs/development-log.md"]);
  const checkpoint = JSON.parse(script("checkpoint.ts", "--agent-dir", agent, "--source", "user", "--type", "docs", "--scope", "config", "--message", "用户已完成配置功能", "--paths-file", file));
  assert.match(checkpoint.message, /^\[user\] docs\(config\):/);
  assert.equal(git(user, "rev-list", "--all", "--count").trim(), "0", "User Project must not auto-commit");
});

// Regression 11: nested Git repositories are reported as directory paths with a trailing slash.
test("review reports nested untracked Git directories without crashing or copying nested .git", (t) => {
  const root = fixture(t);
  const user = join(root, "user"); const agent = join(root, "agent"); const workspace = join(root, "workspace");
  initRepo(user); initRepo(agent); mkdirSync(workspace);
  put(join(agent, "src", "base.txt"), "base\n"); commit(agent, "[agent] feat(core): base");
  put(join(user, "src", "base.txt"), "base\n");
  const nested = join(user, "vendor", "lib");
  initRepo(nested);
  put(join(nested, "README.md"), "nested repository\n");

  const result = JSON.parse(script("review-diff.ts", "review", "--user-dir", user, "--agent-dir", agent, "--workspace", workspace));
  assert.equal(result.status, "DIFFERENT");
  assert.deepEqual(result.extraUserPaths, ["vendor/lib/"]);
  assert.deepEqual(result.unpatchableExtraDirectories, ["vendor/lib/"]);
  assert.equal(readFileSync(result.reports.userExtraPatch).length, 0, "a nested repository directory is not representable as a regular file patch");
  assert.equal(existsSync(join(workspace, ".teaching", "review", "latest", "user-extra", "vendor", "lib")), false,
    "review must not recursively copy an embedded repository and its .git directory");
});

// Regression 12: checkpoint doc parsing must preserve both paths in rename-like status output.
test("checkpoint reports both paths when a core doc is replaced through an unstaged rename", (t) => {
  const root = fixture(t);
  const agent = join(root, "agent"); initRepo(agent);
  put(join(agent, "docs", "development-plan.md"), "line one\nline two\nline three\n");
  commit(agent, "[agent] initial(init): base");
  const oldDoc = join(agent, "docs", "development-plan.md");
  const newDoc = join(agent, "docs", "development-log.md");
  // Intent-to-add keeps the Index tree unchanged while allowing status rename detection.
  renameSync(oldDoc, newDoc);
  git(agent, "add", "-N", "docs/development-log.md");
  assert.equal(spawnSync("git", ["-C", agent, "diff", "--cached", "--quiet"]).status, 0);

  const file = pathsFile(root, ["docs/development-log.md"]);
  const fail = spawnSync(process.execPath, [...nodeFlags, join(skillDir, "scripts", "checkpoint.ts"), "--agent-dir", agent, "--source", "agent", "--type", "feat", "--scope", "docs", "--message", "rename doc", "--paths-file", file], {
    encoding: "utf8", env: { ...process.env, NODE_NO_WARNINGS: "1" },
  });
  assert.notEqual(fail.status, 0);
  assert.match(fail.stderr, /docs\/development-plan\.md/,
    "the deleted source document must not be truncated by porcelain rename parsing");
});
