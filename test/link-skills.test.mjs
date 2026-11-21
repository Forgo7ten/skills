import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  parseArgs,
  expandHome,
  executeLinkOperation,
  AGENT_TARGET_PRESETS,
} from '../scripts/link-skills.mjs';

function createMockSkillsTree(rootDir) {
  const s1 = path.join(rootDir, 'standards', 'skill-one');
  const s2 = path.join(rootDir, 'development', 'docs', 'skill-two');
  fs.mkdirSync(s1, { recursive: true });
  fs.mkdirSync(s2, { recursive: true });
  fs.writeFileSync(path.join(s1, 'SKILL.md'), '---\nname: skill-one\ndescription: one\n---\n');
  fs.writeFileSync(path.join(s2, 'SKILL.md'), '---\nname: skill-two\ndescription: two\n---\n');
  return { s1, s2 };
}

test('parseArgs parses target, agent presets, skill filter and flags', () => {
  const options = parseArgs(['--target', '/custom/path', '--skill', 'my-skill', '--force', '--dry-run']);
  assert.equal(options.force, true);
  assert.equal(options.dryRun, true);
  assert.equal(options.skillName, 'my-skill');
  assert.deepEqual(options.targets, ['/custom/path']);

  const agentOpts = parseArgs(['--agent', 'claude']);
  assert.deepEqual(agentOpts.targets, [path.join(os.homedir(), AGENT_TARGET_PRESETS.claude)]);

  const codexOpts = parseArgs(['--agent', 'codex']);
  assert.deepEqual(codexOpts.targets, [path.join(os.homedir(), AGENT_TARGET_PRESETS.agents)]);

  const geminiOpts = parseArgs(['--agent', 'gemini']);
  assert.deepEqual(geminiOpts.targets, [path.join(os.homedir(), AGENT_TARGET_PRESETS.gemini)]);

  const agyOpts = parseArgs(['--agent', 'antigravity']);
  assert.deepEqual(agyOpts.targets, [path.join(os.homedir(), AGENT_TARGET_PRESETS.antigravity)]);

  const allOpts = parseArgs(['--agent', 'all']);
  assert.equal(allOpts.targets.length, 3);
  assert.deepEqual(allOpts.targets, [
    path.join(os.homedir(), AGENT_TARGET_PRESETS.claude),
    path.join(os.homedir(), AGENT_TARGET_PRESETS.agents),
    path.join(os.homedir(), AGENT_TARGET_PRESETS.gemini),
  ]);

  assert.throws(() => parseArgs(['--unknown']), /Unknown argument/);
});

test('expandHome resolves tilde to home directory', () => {
  assert.equal(expandHome('~/test/path'), path.join(os.homedir(), 'test', 'path'));
  assert.equal(expandHome('/absolute/path'), '/absolute/path');
});

test('executeLinkOperation links skills, respects idempotency and filters by skillName', (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-link-test-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));

  const skillsRoot = path.join(tempDir, 'skills');
  const targetDir = path.join(tempDir, 'agent-target');
  const { s1, s2 } = createMockSkillsTree(skillsRoot);

  const logs = [];
  const logger = {
    log: (msg) => logs.push(msg),
    warn: (msg) => logs.push(`WARN: ${msg}`),
    error: (msg) => logs.push(`ERROR: ${msg}`),
  };

  // 1. Link all skills
  const code1 = executeLinkOperation({
    targets: [targetDir],
    skillsRoot,
    logger,
  });
  assert.equal(code1, 0);

  const link1 = path.join(targetDir, 'skill-one');
  const link2 = path.join(targetDir, 'skill-two');
  assert.ok(fs.lstatSync(link1).isSymbolicLink());
  assert.ok(fs.lstatSync(link2).isSymbolicLink());
  assert.equal(path.resolve(targetDir, fs.readlinkSync(link1)), s1);
  assert.equal(path.resolve(targetDir, fs.readlinkSync(link2)), s2);

  // 2. Idempotent re-run
  logs.length = 0;
  const code2 = executeLinkOperation({
    targets: [targetDir],
    skillsRoot,
    logger,
  });
  assert.equal(code2, 0);
  assert.ok(logs.some((l) => l.includes('[already-linked] skill-one')));

  // 3. Unlink specific skill
  logs.length = 0;
  const code3 = executeLinkOperation({
    targets: [targetDir],
    skillName: 'skill-one',
    unlink: true,
    skillsRoot,
    logger,
  });
  assert.equal(code3, 0);
  assert.equal(fs.existsSync(link1), false);
  assert.equal(fs.existsSync(link2), true);
});

test('executeLinkOperation handles conflicts safely and respects force flag', (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-conflict-test-'));
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }));

  const skillsRoot = path.join(tempDir, 'skills');
  const targetDir = path.join(tempDir, 'agent-target');
  const { s1 } = createMockSkillsTree(skillsRoot);
  fs.mkdirSync(targetDir, { recursive: true });

  // Create a regular directory conflict
  const conflictPath = path.join(targetDir, 'skill-one');
  fs.mkdirSync(conflictPath);
  fs.writeFileSync(path.join(conflictPath, 'user-data.txt'), 'keep me');

  const logs = [];
  const logger = {
    log: (msg) => logs.push(msg),
    warn: (msg) => logs.push(`WARN: ${msg}`),
    error: (msg) => logs.push(`ERROR: ${msg}`),
  };

  // 1. Without force: skip and warn
  executeLinkOperation({
    targets: [targetDir],
    skillName: 'skill-one',
    force: false,
    skillsRoot,
    logger,
  });
  assert.equal(fs.lstatSync(conflictPath).isDirectory(), true);
  assert.ok(logs.some((l) => l.includes('[conflict]')));

  // 2. With force: overwrite regular dir with symlink
  logs.length = 0;
  executeLinkOperation({
    targets: [targetDir],
    skillName: 'skill-one',
    force: true,
    skillsRoot,
    logger,
  });
  assert.equal(fs.lstatSync(conflictPath).isSymbolicLink(), true);
  assert.equal(path.resolve(targetDir, fs.readlinkSync(conflictPath)), s1);
});
