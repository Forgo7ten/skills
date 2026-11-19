import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  extractMarkdownDestinations,
  validateMarkdownLinks,
  validateMarketplace,
} from '../scripts/validate-skills.mjs';
import { isPathInsideRealRoot } from '../scripts/lib/skills.mjs';

test('Markdown link extraction ignores fenced examples and reads inline/reference links', () => {
  const markdown = [
    '[guide](./references/guide.md)',
    '![image](./assets/image.png)',
    '[diagram](./assets/diagram-(final).svg "title")',
    '[ref]: ./references/other.md',
    '```markdown',
    '[not-a-real-link](./missing.md)',
    '```',
  ].join('\n');
  assert.deepEqual(extractMarkdownDestinations(markdown), [
    './references/guide.md',
    './assets/image.png',
    './assets/diagram-(final).svg',
    './references/other.md',
  ]);
});

test('validator checks local links in every Markdown file within a Skill', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-links-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const skillDir = path.join(root, 'sample-skill');
  const referencesDir = path.join(skillDir, 'references');
  fs.mkdirSync(referencesDir, { recursive: true });
  fs.writeFileSync(path.join(skillDir, 'SKILL.md'), '[guide](references/guide.md)\n');
  fs.writeFileSync(path.join(referencesDir, 'guide.md'), '[missing](missing.md)\n');

  const skill = { absDir: skillDir };
  const errors = [];
  validateMarkdownLinks(skill, [
    path.join(skillDir, 'SKILL.md'),
    path.join(referencesDir, 'guide.md'),
  ], errors);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /referenced local file does not exist.*missing\.md/);
});

test('isPathInsideRealRoot resolves symlinks and rejects paths escaping the Skill root', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-realpath-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const skillDir = path.join(root, 'sample-skill');
  const outsideFile = path.join(root, 'outside.md');
  const linkInside = path.join(skillDir, 'references', 'escape.md');
  fs.mkdirSync(path.join(skillDir, 'references'), { recursive: true });
  fs.writeFileSync(outsideFile, 'outside\n');
  fs.symlinkSync(outsideFile, linkInside);

  assert.equal(isPathInsideRealRoot(skillDir, path.join(skillDir, 'references', 'guide.md')), true);
  assert.equal(isPathInsideRealRoot(skillDir, path.join(skillDir, '..', 'outside.md')), false);
  assert.equal(isPathInsideRealRoot(skillDir, linkInside), false);
});

test('marketplace validation detects metadata drift, not only Skill path drift', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'marketplace-drift-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'marketplace.json');
  fs.writeFileSync(file, JSON.stringify({ name: 'old-name', plugins: [] }));

  const errors = [];
  validateMarketplace(errors, { name: 'new-name', plugins: [] }, file);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /does not match manifest\.json \+ skills/);
});

test('Markdown link extraction handles escaping, nested ](, and multiline reference definitions', () => {
  const destinations = extractMarkdownDestinations([
    '[inline](references/a\\\\\\(b\\).md)',
    '\\[escaped\\](missing.md)',
    '[literal](<a](b).md>)',
    '[ref-escaped]: references/a\\-b.md',
    '[ref-multiline]:',
    '  references/guide.md',
  ].join('\n'));

  assert.deepEqual(destinations, [
    'references/a\\(b).md',
    'a](b).md',
    'references/a-b.md',
    'references/guide.md',
  ]);
});

test('validateMarkdownLinks resolves escaped reference links and skips templates/tests directories', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'skill-links-edge-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));

  fs.mkdirSync(path.join(dir, 'references'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'templates'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'tests'), { recursive: true });

  const guideTarget = path.join(dir, 'references', 'a-b.md');
  const skillMd = path.join(dir, 'SKILL.md');
  const templateMd = path.join(dir, 'templates', 'sample.md');
  const testMd = path.join(dir, 'tests', 'fixture.md');

  fs.writeFileSync(guideTarget, '# ok\n');
  fs.writeFileSync(skillMd, '[doc][ref]\n\\[escaped\\](missing.md)\n\n[ref]:\n  references/a\\-b.md\n');
  fs.writeFileSync(templateMd, '[placeholder](does-not-exist.md)\n');
  fs.writeFileSync(testMd, '[fixture](also-missing.md)\n');

  const errors = [];
  validateMarkdownLinks(
    { relDir: 'sample/skill', absDir: dir },
    [skillMd, guideTarget, templateMd, testMd],
    errors,
  );
  assert.deepEqual(errors, []);
});
