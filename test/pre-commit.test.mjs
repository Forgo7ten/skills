import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const HOOK_SOURCE = path.join(REPO_ROOT, '.githooks', 'pre-commit');

function runGit(cwd, args, env = process.env) {
  return execFileSync('git', args, { cwd, env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
}

function createFixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-hook-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, '.githooks'), { recursive: true });
  fs.mkdirSync(path.join(root, '.claude-plugin'), { recursive: true });
  fs.mkdirSync(path.join(root, 'scripts'), { recursive: true });
  fs.mkdirSync(path.join(root, 'skills', 'development', 'docs', 'sample-skill'), { recursive: true });
  fs.copyFileSync(HOOK_SOURCE, path.join(root, '.githooks', 'pre-commit'));
  fs.chmodSync(path.join(root, '.githooks', 'pre-commit'), 0o755);

  fs.writeFileSync(path.join(root, 'manifest.json'), '{"name":"fixture"}\n');
  fs.writeFileSync(path.join(root, '.claude-plugin', 'marketplace.json'), '{"plugins":[]}\n');
  fs.writeFileSync(path.join(root, 'skills', 'development', 'docs', 'sample-skill', 'SKILL.md'), '---\nname: sample-skill\ndescription: fixture\n---\n');
  fs.writeFileSync(path.join(root, 'scripts', 'sync-marketplace.mjs'), '');
  fs.writeFileSync(path.join(root, 'scripts', 'validate-skills.mjs'), '');

  const bin = path.join(root, 'test-bin');
  fs.mkdirSync(bin);
  const fakePnpm = path.join(bin, 'pnpm');
  fs.writeFileSync(fakePnpm, `#!/bin/sh
case "$1 $2" in
  'run skills:sync')
    printf '{"plugins":[{"name":"generated"}]}\\n' > .claude-plugin/marketplace.json
    ;;
  'run skills:check')
    echo validated >> hook.log
    ;;
  *)
    echo "unexpected pnpm invocation: $*" >&2
    exit 9
    ;;
esac
`);
  fs.chmodSync(fakePnpm, 0o755);

  const env = { ...process.env, PATH: `${bin}${path.delimiter}${process.env.PATH}` };
  runGit(root, ['init', '-q']);
  runGit(root, ['config', 'user.name', 'Skills Hook Test']);
  runGit(root, ['config', 'user.email', 'skills-hook@example.invalid']);
  runGit(root, ['add', '.']);
  runGit(root, ['commit', '-m', 'initial', '--quiet']);
  runGit(root, ['config', 'core.hooksPath', '.githooks']);

  return { root, env };
}

test('pre-commit regenerates marketplace and stages the generated file', (t) => {
  const { root, env } = createFixture(t);
  fs.writeFileSync(
    path.join(root, 'skills', 'development', 'docs', 'sample-skill', 'SKILL.md'),
    '---\nname: sample-skill\ndescription: changed\n---\n',
  );
  runGit(root, ['add', 'skills']);
  runGit(root, ['commit', '-m', 'change skill'], env);

  const committedMarketplace = runGit(root, ['show', 'HEAD:.claude-plugin/marketplace.json']);
  assert.match(committedMarketplace, /generated/);
  assert.equal(fs.readFileSync(path.join(root, 'hook.log'), 'utf8').trim(), 'validated');
});

test('pre-commit blocks when staged Skill content has additional unstaged edits', (t) => {
  const { root, env } = createFixture(t);
  const skillFile = path.join(root, 'skills', 'development', 'docs', 'sample-skill', 'SKILL.md');
  fs.writeFileSync(skillFile, '---\nname: sample-skill\ndescription: staged\n---\n');
  runGit(root, ['add', 'skills']);
  fs.writeFileSync(skillFile, '---\nname: sample-skill\ndescription: unstaged\n---\n');

  const result = spawnSync('git', ['commit', '-m', 'must fail'], {
    cwd: root,
    env,
    encoding: 'utf8',
  });
  assert.notEqual(result.status, 0);
  assert.match(`${result.stdout}\n${result.stderr}`, /unstaged changes exist/);
});

test('pre-commit blocks untracked files under skills when a Skill change is staged', (t) => {
  const { root, env } = createFixture(t);
  const skillFile = path.join(root, 'skills', 'development', 'docs', 'sample-skill', 'SKILL.md');
  fs.writeFileSync(skillFile, '---\nname: sample-skill\ndescription: changed\n---\n');
  runGit(root, ['add', 'skills']);
  fs.writeFileSync(path.join(root, 'skills', 'development', 'docs', 'sample-skill', 'extra.md'), 'untracked');

  const result = spawnSync('git', ['commit', '-m', 'must fail'], {
    cwd: root,
    env,
    encoding: 'utf8',
  });
  assert.notEqual(result.status, 0);
  assert.match(`${result.stdout}\n${result.stderr}`, /untracked files exist under skills/);
});

test('pre-commit skips commits unrelated to Skill sources', (t) => {
  const { root, env } = createFixture(t);
  fs.writeFileSync(path.join(root, 'README.md'), 'unrelated documentation change\n');
  runGit(root, ['add', 'README.md']);
  runGit(root, ['commit', '-m', 'docs: update unrelated readme'], env);
  assert.equal(fs.existsSync(path.join(root, 'hook.log')), false);
});
