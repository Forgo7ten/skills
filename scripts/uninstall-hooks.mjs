import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { normalizeScriptArgs } from './lib/cli-args.mjs';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EXPECTED_HOOKS_PATH = '.githooks';

function git(args) {
  return execFileSync('git', args, {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

function main() {
  if (normalizeScriptArgs(process.argv.slice(2)).length > 0) {
    console.error('Usage: pnpm run hooks:uninstall');
    return 2;
  }

  // Check the repository first so an unset config is not confused with a
  // directory that is not a Git worktree.
  let gitRoot;
  try {
    gitRoot = git(['rev-parse', '--show-toplevel']);
  } catch (error) {
    if (error.code === 'ENOENT') {
      console.error('Git executable not found. Install Git before uninstalling project hooks.');
    } else {
      console.error('This directory is not inside a Git working tree.');
    }
    return 1;
  }

  if (path.resolve(gitRoot) !== REPO_ROOT) {
    console.error(`Run this command from the repository root: ${gitRoot}`);
    return 1;
  }

  let current;
  try {
    current = git(['config', '--local', '--get', 'core.hooksPath']);
  } catch (error) {
    // git config --get returns status 1 only when the key is not set.
    if (error.status === 1) {
      console.log('Git hooks are not installed. Nothing to do.');
      return 0;
    }

    console.error(`Unable to read repository Git hook configuration: ${error.message}`);
    return 1;
  }

  if (current !== EXPECTED_HOOKS_PATH) {
    console.log(
      `Not changing Git config: core.hooksPath is ${current || '(unset)'}, ` +
      `not ${EXPECTED_HOOKS_PATH}.`,
    );
    return 0;
  }

  try {
    git(['config', '--local', '--unset', 'core.hooksPath']);
  } catch (error) {
    console.error(`Unable to remove repository Git hook configuration: ${error.message}`);
    return 1;
  }

  console.log('Removed repository-local core.hooksPath configuration.');
  return 0;
}

process.exitCode = main();
