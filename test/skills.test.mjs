import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  createMarketplace,
  parseFrontmatter,
  scanSkillsTree,
} from '../scripts/lib/skills.mjs';

test('frontmatter parser supports folded scalars, quoted values, and nested mappings', () => {
  const source = [
    '---',
    'name: sample-skill',
    'description: >-',
    '  First line',
    '  second line',
    'metadata:',
    '  platform: macos',
    '  enabled: true',
    '---',
    '# Sample',
  ].join('\n');
  const result = parseFrontmatter(source);
  assert.equal(result.error, null);
  assert.equal(result.data.name, 'sample-skill');
  assert.equal(result.data.description, 'First line second line');
  assert.deepEqual(result.data.metadata, { platform: 'macos', enabled: true });
});

test('frontmatter parser rejects duplicate keys and malformed YAML', () => {
  const duplicate = parseFrontmatter('---\nname: first\nname: second\ndescription: test\n---\n');
  assert.match(duplicate.error ?? '', /Map keys must be unique|already set|duplicate/i);

  const malformed = parseFrontmatter('---\nname: [unterminated\ndescription: test\n---\n');
  assert.match(malformed.error ?? '', /unexpected end|flow sequence|YAML/i);
});

test('marketplace generation groups and sorts Skills deterministically', () => {
  const skills = [
    { groupName: 'platform-macos', category: 'platform', subcategory: 'macos', manifestPath: './skills/platform/macos/z-skill' },
    { groupName: 'platform-macos', category: 'platform', subcategory: 'macos', manifestPath: './skills/platform/macos/a-skill' },
    { groupName: 'development-docs', category: 'development', subcategory: 'docs', manifestPath: './skills/development/docs/check-mermaid' },
  ];
  const marketplace = createMarketplace(skills, { name: 'test-repo' }, { 'platform-macos': 'macOS tools' });
  assert.deepEqual(marketplace.plugins.map((plugin) => plugin.name), ['development-docs', 'platform-macos']);
  assert.deepEqual(marketplace.plugins[1].skills, [
    './skills/platform/macos/a-skill',
    './skills/platform/macos/z-skill',
  ]);
  assert.equal(marketplace.plugins[0].description, 'development docs skills');
});

test('scanner reports ambiguous generated group IDs instead of merging categories', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-tree-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  const skillPaths = [
    ['a-b', 'c', 'skill-one'],
    ['a', 'b-c', 'skill-two'],
  ];
  for (const parts of skillPaths) {
    const dir = path.join(root, ...parts);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'SKILL.md'), '---\nname: test\ndescription: test\n---\n');
  }

  const result = scanSkillsTree(root);
  assert.ok(result.errors.some((error) => error.includes('Plugin group ID collision')));
});

test('scanner discovers two-level and three-level skills and derives appropriate groups', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'skills-mixed-depth-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  // Two-level: root/standards/git-commit-message/SKILL.md
  const twoLevel = path.join(root, 'standards', 'git-commit-message');
  fs.mkdirSync(twoLevel, { recursive: true });
  fs.writeFileSync(path.join(twoLevel, 'SKILL.md'), '---\nname: git-commit-message\ndescription: test\n---\n');

  // Three-level: root/development/docs/check-mermaid/SKILL.md
  const threeLevel = path.join(root, 'development', 'docs', 'check-mermaid');
  fs.mkdirSync(threeLevel, { recursive: true });
  fs.writeFileSync(path.join(threeLevel, 'SKILL.md'), '---\nname: check-mermaid\ndescription: test\n---\n');

  const { skills, errors } = scanSkillsTree(root);
  assert.deepEqual(errors, []);
  assert.equal(skills.length, 2);

  const two = skills.find((s) => s.skillDirName === 'git-commit-message');
  assert.ok(two);
  assert.equal(two.groupName, 'standards');
  assert.equal(two.manifestPath, './skills/standards/git-commit-message');
  assert.equal(two.subcategory, null);

  const three = skills.find((s) => s.skillDirName === 'check-mermaid');
  assert.ok(three);
  assert.equal(three.groupName, 'development-docs');
  assert.equal(three.manifestPath, './skills/development/docs/check-mermaid');
  assert.equal(three.subcategory, 'docs');
});
