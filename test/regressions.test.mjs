import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  extractMarkdownDestinations,
  validateMarkdownLinks,
} from '../scripts/validate-skills.mjs';
import { scanSkillsTree } from '../scripts/lib/skills.mjs';

function makeSkill(t, subpath = ['sample']) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-regression-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const dir = path.join(root, ...subpath);
  fs.mkdirSync(path.join(dir, 'references'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'scripts'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'SKILL.md'), '# sample\n');
  fs.writeFileSync(path.join(dir, 'references', 'guide.md'), '# guide\n');
  return dir;
}

function linkErrors(skillDir, markdown) {
  const md = path.join(skillDir, 'SKILL.md');
  fs.writeFileSync(md, markdown);
  const errors = [];
  validateMarkdownLinks({ absDir: skillDir }, [md], errors);
  return errors;
}

test('P1: Windows drive absolute links are rejected for both separators', (t) => {
  const dir = makeSkill(t);
  for (const target of ['C:/Users/me/secret.txt', String.raw`C:\Users\me\secret.txt`]) {
    const errors = linkErrors(dir, `[x](${target})\n`);
    assert.equal(errors.length, 1, `${target}: ${errors}`);
    assert.match(errors[0], /absolute local link is not portable/);
  }
  assert.deepEqual(linkErrors(dir, '[x](https://example.com/path)\n'), []);
});

test('P2: inline and reference Windows-style relative links retain backslashes', (t) => {
  const dir = makeSkill(t);
  assert.deepEqual(extractMarkdownDestinations(String.raw`[g](references\guide.md)`), [String.raw`references\guide.md`]);
  assert.deepEqual(linkErrors(dir, String.raw`[g](references\guide.md)`), []);
  assert.deepEqual(linkErrors(dir, String.raw`[g]: references\guide.md`), []);
  assert.deepEqual(extractMarkdownDestinations(String.raw`[g](references\/guide.md)`), ['references/guide.md']);
});

test('P5: unreadable/missing input produces diagnostics instead of throwing', (t) => {
  const dir = makeSkill(t);
  const errors = [];
  validateMarkdownLinks({ absDir: dir }, [path.join(dir, 'references', 'missing.md')], errors);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /cannot read file/);
});

test('P5: mode 000 file and directory produce diagnostics when unprivileged', { skip: process.getuid?.() === 0 }, (t) => {
  const dir = makeSkill(t, ['platform', 'macos', 'sample']);
  const root = path.dirname(path.dirname(path.dirname(dir)));
  const lockedFile = path.join(dir, 'references', 'locked.md');
  const lockedDir = path.join(dir, 'references', 'locked-dir');
  fs.writeFileSync(lockedFile, '# test\n');
  fs.mkdirSync(lockedDir);
  fs.chmodSync(lockedFile, 0o000);
  fs.chmodSync(lockedDir, 0o000);
  try {
    const fileErrors = [];
    validateMarkdownLinks({ absDir: dir }, [lockedFile], fileErrors);
    assert.ok(fileErrors.some((x) => x.includes('cannot read file')), fileErrors.join('\n'));

    const dirErrors = scanSkillsTree(root).errors;
    assert.ok(dirErrors.some((e) => e.includes('cannot read directory') && e.includes('locked-dir')), dirErrors.join('\n'));
  } finally {
    fs.chmodSync(lockedFile, 0o644);
    fs.chmodSync(lockedDir, 0o755);
  }
});
