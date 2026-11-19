import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { normalizeScriptArgs } from './lib/cli-args.mjs';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EXPECTED_HOOKS_PATH = '.githooks';

function git(args, options = {}) {
  return execFileSync('git', args, {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    ...options,
  }).trim();
}

function main() {
  const args = normalizeScriptArgs(process.argv.slice(2));
  if (args.length > 1 || args.some((arg) => arg !== '--force')) {
    console.error('Usage: pnpm run hooks:install [-- --force]');
    return 2;
  }
  const force = args.includes('--force');

  const hookFile = path.join(REPO_ROOT, EXPECTED_HOOKS_PATH, 'pre-commit');
  if (!fs.existsSync(hookFile)) {
    console.error(`Hook file is missing: ${hookFile}`);
    return 1;
  }

  let gitRoot;
  try {
    gitRoot = git(['rev-parse', '--show-toplevel']);
  } catch {
    console.error('This directory is not inside a Git working tree.');
    return 1;
  }

  if (path.resolve(gitRoot) !== REPO_ROOT) {
    console.error(`Run this command from the repository root: ${gitRoot}`);
    return 1;
  }

  let current = '';
  try {
    current = git(['config', '--local', '--get', 'core.hooksPath']);
  } catch {
    // No repository-local hooksPath is configured.
  }

  if (current && current !== EXPECTED_HOOKS_PATH && !force) {
    console.error(`A different repository-local core.hooksPath is already configured: ${current}`);
    console.error(`Use --force to replace it with ${EXPECTED_HOOKS_PATH}.`);
    return 1;
  }

  try {
    git(['config', '--local', 'core.hooksPath', EXPECTED_HOOKS_PATH]);
  } catch (error) {
    console.error(`Failed to configure Git hooks: ${error.message}`);
    return 1;
  }

  console.log(`Installed repository Git hooks: core.hooksPath=${EXPECTED_HOOKS_PATH}`);
  let globalHooksPath = '';
  try {
    globalHooksPath = git(['config', '--global', '--get', 'core.hooksPath']);
  } catch {
    // No global hooksPath is configured.
  }
  if (globalHooksPath) {
    console.warn(`Note: this repository-local setting takes precedence over global core.hooksPath=${globalHooksPath}.`);
    console.warn('Global hooks will not run in this repository unless you chain them from .githooks/pre-commit.');
  }
  console.log('The pre-commit hook will regenerate and stage marketplace.json when its source changes.');
  return 0;
}

process.exitCode = main();
