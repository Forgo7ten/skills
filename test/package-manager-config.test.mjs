import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const packageJson = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'package.json'), 'utf8'));
const workflow = fs.readFileSync(path.join(REPO_ROOT, '.github/workflows/ci.yml'), 'utf8');

test('package manager pin, engines range, and CI version stay aligned', () => {
  assert.equal(packageJson.packageManager, 'pnpm@11.4.0');
  assert.equal(packageJson.engines.node, '>=22.16.0');
  assert.equal(packageJson.engines.pnpm, '>=11.4.0 <12');
  assert.match(workflow, /uses: pnpm\/action-setup@v6[\s\S]*?version: 11\.4\.0/);
  assert.match(workflow, /pnpm install --frozen-lockfile/);
});

test('pnpm lockfile remains on the v9 format supported by pnpm 11', () => {
  const lockfile = fs.readFileSync(path.join(REPO_ROOT, 'pnpm-lock.yaml'), 'utf8');
  assert.match(lockfile, /^lockfileVersion: ['"]?9\.0['"]?$/m);
});

test('documentation does not retain the previous pnpm 10.34.9 pin', () => {
  for (const relative of ['README.md', 'docs/architecture.md', 'docs/development.md']) {
    const filePath = path.join(REPO_ROOT, relative);
    assert.ok(fs.existsSync(filePath), `${relative} is missing`);
    const content = fs.readFileSync(filePath, 'utf8');
    assert.doesNotMatch(content, /pnpm(?:@|\s+)10\.34\.9/);
  }
});
