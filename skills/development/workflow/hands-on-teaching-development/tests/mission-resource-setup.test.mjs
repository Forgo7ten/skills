import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const skillDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function resolveNodeFlags() {
  if (process.env.TEACHING_NATIVE_TS === '1') return ['--experimental-strip-types'];
  try {
    return ['--import', pathToFileURL(createRequire(import.meta.url).resolve('tsx')).href];
  } catch {
    const [major, minor] = process.versions.node.split('.').map(Number);
    if (major > 22 || (major === 22 && minor >= 6)) return ['--experimental-strip-types'];
    throw new Error('未检测到 tsx，且当前 Node 版本不支持 --experimental-strip-types');
  }
}

const flags = resolveNodeFlags();
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'teaching-docs-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const gitConfig = join(root, 'gitconfig');
  writeFileSync(gitConfig, '[user]\n name = Learning Agent\n email = learn@example.invalid\n');
  const env = { ...process.env, GIT_CONFIG_GLOBAL: gitConfig, NODE_NO_WARNINGS: '1' };
  return { root, env, workspace: join(root, 'workspace'), user: join(root, 'user'), example: join(root, 'example') };
}
function invoke(env, script, args, expectCode = 0) {
  const p = spawnSync(process.execPath, [...flags, join(skillDir, 'scripts', script), ...args], { env, encoding: 'utf8' });
  assert.equal(p.status, expectCode, `exit ${p.status}: ${script} ${args.join(' ')}\n${p.stdout}\n${p.stderr}`);
  return p.stdout;
}
function git(env, repo, ...args) {
  const p = spawnSync('git', ['-C', repo, ...args], { env, encoding: 'utf8' });
  assert.equal(p.status, 0, `git ${args.join(' ')}\n${p.stderr}`);
  return p.stdout;
}
function put(path, text) { mkdirSync(dirname(path), {recursive: true}); writeFileSync(path, text); }

test('initial Agent Project creates and commits MISSION/RESOURCES; workspace AGENTS stays outside', t => {
  const f = fixture(t);
  put(join(f.example, 'README.md'), '# example\n');
  put(join(f.user, 'src', 'main.ts'), 'export const demo=1;\n');
  invoke(f.env, 'setup.ts', ['workspace', '--workspace', f.workspace, '--example', f.example, '--user-project', f.user]);
  invoke(f.env, 'setup.ts', ['user-baseline', '--workspace', f.workspace]);
  invoke(f.env, 'setup.ts', ['agent-init', '--workspace', f.workspace, '--agent-name', 'my-product']);
  const agent = join(f.workspace, 'my-product');
  const initial = git(f.env, agent, 'show', '--pretty=format:', '--name-only', 'HEAD').trim().split('\n');
  for (const path of ['docs/MISSION.md', 'docs/RESOURCES.md', 'docs/NOTES.md', 'docs/development-plan.md', 'docs/teaching-state.md']) {
    assert(existsSync(join(agent, path)), path);
    assert(initial.includes(path), `${path} should be included in initial commit`);
  }
  assert(existsSync(join(f.workspace, 'AGENTS.md')));
  assert(!existsSync(join(f.user, 'docs', 'MISSION.md')));
  assert.match(readFileSync(join(agent, 'docs', 'MISSION.md'), 'utf8'), /为什么要做/);
});

test('checkpoint refuses to omit modified MISSION/RESOURCES from the code checkpoint', t => {
  const f = fixture(t);
  put(join(f.example, 'README.md'), '# example\n');
  put(join(f.user, 'src', 'main.ts'), 'export const demo=1;\n');
  invoke(f.env, 'setup.ts', ['workspace', '--workspace', f.workspace, '--example', f.example, '--user-project', f.user]);
  invoke(f.env, 'setup.ts', ['user-baseline', '--workspace', f.workspace]);
  invoke(f.env, 'setup.ts', ['agent-init', '--workspace', f.workspace, '--agent-name', 'my-product']);
  const agent = join(f.workspace, 'my-product');
  put(join(agent, 'src', 'main.ts'), 'export const demo=2;\n');
  put(join(agent, 'docs', 'MISSION.md'), '# Mission\n用户确认的新目标\n');
  put(join(agent, 'docs', 'RESOURCES.md'), '# Resources\nExample A: src/Main\n');
  const list = join(f.root, 'files.json');
  writeFileSync(list, JSON.stringify(['src/main.ts']));
  const args = ['--agent-dir', agent, '--source', 'agent', '--type', 'feat', '--scope', 'core', '--message', '完善核心实现', '--paths-file', list];
  const invalid = spawnSync(process.execPath, [...flags, join(skillDir, 'scripts', 'checkpoint.ts'), ...args], {env:f.env, encoding:'utf8'});
  assert.notEqual(invalid.status, 0);
  assert.match(invalid.stderr, /docs\/MISSION.md/);
  assert.match(invalid.stderr, /docs\/RESOURCES.md/);
  writeFileSync(list, JSON.stringify(['src/main.ts', 'docs/MISSION.md', 'docs/RESOURCES.md']));
  const submitted = JSON.parse(invoke(f.env, 'checkpoint.ts', args));
  assert.equal(submitted.status, 'COMMITTED');
  assert.deepEqual(submitted.files.sort(), ['docs/MISSION.md','docs/RESOURCES.md','src/main.ts']);
});
