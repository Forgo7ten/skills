import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { normalizeScriptArgs } from '../scripts/lib/cli-args.mjs';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function runSync(args) {
  return spawnSync(process.execPath, [path.join(REPO_ROOT, 'scripts/sync-marketplace.mjs'), ...args], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
  });
}

test('normalizeScriptArgs removes only one leading package-manager separator', () => {
  assert.deepEqual(normalizeScriptArgs(['--', '--check']), ['--check']);
  assert.deepEqual(normalizeScriptArgs(['--check']), ['--check']);
  assert.deepEqual(normalizeScriptArgs(['--', '--check', '--']), ['--check', '--']);
  assert.deepEqual(normalizeScriptArgs(['--check', '--']), ['--check', '--']);
});

test('sync CLI accepts --check with and without forwarded separator', () => {
  for (const args of [['--check'], ['--', '--check']]) {
    const result = runSync(args);
    assert.equal(result.status, 0, `${args.join(' ')} failed:\n${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout, /is up to date/);
  }
});

test('sync CLI rejects unknown arguments after separator normalization', () => {
  const result = runSync(['--', '--unknown']);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /Unknown argument: --unknown/);
});
