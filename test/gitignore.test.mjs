import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function git(cwd, args) {
  return spawnSync('git', args, { cwd, encoding: 'utf8' });
}

test('.gitignore ignores dependencies, OS files, logs, environments, and caches', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-gitignore-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.copyFileSync(path.join(REPO_ROOT, '.gitignore'), path.join(root, '.gitignore'));
  execFileSync('git', ['init', '-q'], { cwd: root });

  for (const file of [
    'node_modules/',
    '.DS_Store',
    'Thumbs.db',
    'app.log',
    '.env',
    '.env.local',
    'dist/',
    'build/',
    '__pycache__/',
    'temp.pyc',
  ]) {
    const result = git(root, ['check-ignore', '--no-index', '-q', file]);
    assert.equal(result.status, 0, `${file} should be ignored`);
  }
});
