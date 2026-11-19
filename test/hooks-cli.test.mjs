import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SCRIPT_FILES = [
  'scripts/install-hooks.mjs',
  'scripts/uninstall-hooks.mjs',
  'scripts/lib/cli-args.mjs',
];

function git(cwd, args) {
  return execFileSync('git', args, {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

function createRepo(t, { initialize = true } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-hooks-cli-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  for (const relative of SCRIPT_FILES) {
    const destination = path.join(root, relative);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(path.join(REPO_ROOT, relative), destination);
  }
  const hook = path.join(root, '.githooks', 'pre-commit');
  fs.mkdirSync(path.dirname(hook), { recursive: true });
  fs.writeFileSync(hook, '#!/bin/sh\nexit 0\n');
  fs.chmodSync(hook, 0o755);

  if (initialize) git(root, ['init', '-q']);
  return root;
}

function runScript(root, relative, args = [], env = process.env) {
  return spawnSync(process.execPath, [path.join(root, relative), ...args], {
    cwd: root,
    env,
    encoding: 'utf8',
  });
}

function hooksPath(root) {
  try {
    return git(root, ['config', '--local', '--get', 'core.hooksPath']);
  } catch (error) {
    if (error.status === 1) return null;
    throw error;
  }
}

test('hooks:install accepts --force with package-manager separator', (t) => {
  const root = createRepo(t);
  git(root, ['config', '--local', 'core.hooksPath', 'custom-hooks']);

  const result = runScript(root, 'scripts/install-hooks.mjs', ['--', '--force']);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.equal(hooksPath(root), '.githooks');
});

test('hooks:install accepts --force without package-manager separator', (t) => {
  const root = createRepo(t);
  git(root, ['config', '--local', 'core.hooksPath', 'custom-hooks']);

  const result = runScript(root, 'scripts/install-hooks.mjs', ['--force']);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.equal(hooksPath(root), '.githooks');
});

test('hooks:install does not replace a different hooksPath without --force', (t) => {
  const root = createRepo(t);
  git(root, ['config', '--local', 'core.hooksPath', 'custom-hooks']);

  const result = runScript(root, 'scripts/install-hooks.mjs');
  assert.equal(result.status, 1);
  assert.equal(hooksPath(root), 'custom-hooks');
});

test('hooks:install rejects unknown and duplicate arguments', (t) => {
  const root = createRepo(t);

  const unknown = runScript(root, 'scripts/install-hooks.mjs', ['--', '--unknown']);
  assert.equal(unknown.status, 2);
  assert.match(unknown.stderr, /Usage:/);

  const duplicate = runScript(root, 'scripts/install-hooks.mjs', ['--force', '--force']);
  assert.equal(duplicate.status, 2);
});

test('hooks:uninstall is idempotent when core.hooksPath is unset', (t) => {
  const root = createRepo(t);
  const result = runScript(root, 'scripts/uninstall-hooks.mjs');
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /not installed|Nothing to do/i);
  assert.equal(hooksPath(root), null);
});

test('hooks:uninstall removes only the project-managed .githooks value', (t) => {
  const root = createRepo(t);
  git(root, ['config', '--local', 'core.hooksPath', 'custom-hooks']);

  const untouched = runScript(root, 'scripts/uninstall-hooks.mjs');
  assert.equal(untouched.status, 0, `${untouched.stdout}\n${untouched.stderr}`);
  assert.equal(hooksPath(root), 'custom-hooks');

  git(root, ['config', '--local', 'core.hooksPath', '.githooks']);
  const removed = runScript(root, 'scripts/uninstall-hooks.mjs');
  assert.equal(removed.status, 0, `${removed.stdout}\n${removed.stderr}`);
  assert.equal(hooksPath(root), null);
});

test('hooks:uninstall reports a non-Git directory without treating it as unset config', (t) => {
  const root = createRepo(t, { initialize: false });
  const result = runScript(root, 'scripts/uninstall-hooks.mjs');
  assert.equal(result.status, 1);
  assert.match(result.stderr, /not inside a Git working tree/i);
});

test('hooks:uninstall reports a missing Git executable separately', (t) => {
  const root = createRepo(t);
  const emptyPath = path.join(root, 'empty-path');
  fs.mkdirSync(emptyPath);
  const result = runScript(root, 'scripts/uninstall-hooks.mjs', [], {
    ...process.env,
    PATH: emptyPath,
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Git executable not found/i);
});

test('hooks:uninstall refuses to alter the parent repository when run from a nested copy', (t) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-hooks-parent-'));
  t.after(() => fs.rmSync(parent, { recursive: true, force: true }));
  git(parent, ['init', '-q']);
  git(parent, ['config', '--local', 'core.hooksPath', '.githooks']);

  const nested = path.join(parent, 'nested-project');
  fs.mkdirSync(nested, { recursive: true });
  for (const relative of SCRIPT_FILES) {
    const destination = path.join(nested, relative);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(path.join(REPO_ROOT, relative), destination);
  }

  const result = runScript(nested, 'scripts/uninstall-hooks.mjs');
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Run this command from the repository root/);
  assert.equal(git(parent, ['config', '--local', '--get', 'core.hooksPath']), '.githooks');
});

test('P8: hooks:uninstall rejects unknown arguments without mutating Git config', (t) => {
  const root = createRepo(t);
  git(root, ['config', '--local', 'core.hooksPath', '.githooks']);
  for (const args of [['--bogus'], ['--', '--bogus', 'extra']]) {
    const result = runScript(root, 'scripts/uninstall-hooks.mjs', args);
    assert.equal(result.status, 2, `${result.stdout}\n${result.stderr}`);
    assert.equal(hooksPath(root), '.githooks');
  }
});
