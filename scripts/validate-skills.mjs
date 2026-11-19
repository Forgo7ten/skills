import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import {
  IGNORED_DIRS,
  KEBAB_CASE_RE,
  MARKETPLACE_PATH,
  SKILLS_ROOT,
  createMarketplace,
  isPathInsideRealRoot,
  loadMarketplaceInfo,
  parseFrontmatter,
  scanSkillsTree,
  toRepoRelative,
} from './lib/skills.mjs';

function collectFiles(dir, files = [], errors = []) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (err) {
    errors.push(`${toRepoRelative(dir)}: cannot read directory: ${err.message}`);
    return files;
  }

  for (const entry of entries) {
    if (IGNORED_DIRS.has(entry.name)) continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) {
      errors.push(`${toRepoRelative(fullPath)}: symbolic links are not allowed inside a Skill`);
    } else if (entry.isDirectory()) {
      collectFiles(fullPath, files, errors);
    } else if (entry.isFile()) {
      files.push(fullPath);
    }
  }
  return files;
}

function readTextFile(filePath, errors) {
  let buffer;
  try {
    buffer = fs.readFileSync(filePath);
  } catch (err) {
    errors.push(`${toRepoRelative(filePath)}: cannot read file: ${err.message}`);
    return null;
  }
  if (buffer.includes(0)) return null;
  return buffer.toString('utf8');
}

function stripMarkdownFences(markdown) {
  const lines = markdown.split(/\r\n|\n|\r/);
  let active = null;
  return lines.map((line) => {
    if (active) {
      const close = new RegExp(`^ {0,3}${active.char}{${active.length},}[ \\t]*$`);
      if (close.test(line)) active = null;
      return '';
    }
    const open = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (open) {
      const fence = open[1];
      if (fence[0] === '`' && open[2].includes('`')) return line;
      active = { char: fence[0], length: fence.length };
      return '';
    }
    return line;
  }).join('\n');
}

// CommonMark backslash escapes apply only before ASCII punctuation.
const ASCII_PUNCTUATION_RE = /[!-/:-@[-`{-~]/;

function isEscapingBackslash(source, index) {
  return source[index] === '\\' &&
    index + 1 < source.length &&
    ASCII_PUNCTUATION_RE.test(source[index + 1]);
}

function hasOddPrecedingBackslashes(source, index) {
  let count = 0;
  for (let cursor = index - 1; cursor >= 0 && source[cursor] === '\\'; cursor -= 1) {
    count += 1;
  }
  return count % 2 === 1;
}

function unescapeMarkdownPunctuation(text) {
  let result = '';
  for (let index = 0; index < text.length; index += 1) {
    if (isEscapingBackslash(text, index)) {
      result += text[index + 1];
      index += 1;
    } else {
      result += text[index];
    }
  }
  return result;
}

export function extractMarkdownDestinations(markdown) {
  const source = stripMarkdownFences(markdown).replace(/(`+)[\s\S]*?\1/g, '');
  const destinations = [];

  for (let index = 0; index < source.length - 1; index += 1) {
    if (source[index] !== ']' || source[index + 1] !== '(') continue;
    if (hasOddPrecedingBackslashes(source, index)) continue;

    let cursor = index + 2;
    while (cursor < source.length && /\s/.test(source[cursor])) cursor += 1;
    if (cursor >= source.length) continue;

    let destination = '';
    if (source[cursor] === '<') {
      cursor += 1;
      while (cursor < source.length && source[cursor] !== '>' && source[cursor] !== '\n') {
        if (isEscapingBackslash(source, cursor)) {
          destination += source[cursor + 1];
          cursor += 2;
        } else {
          destination += source[cursor];
          cursor += 1;
        }
      }
      if (source[cursor] === '>') {
        destinations.push(destination);
        index = cursor;
      }
      continue;
    }

    let depth = 0;
    while (cursor < source.length) {
      const char = source[cursor];
      if (isEscapingBackslash(source, cursor)) {
        destination += source[cursor + 1];
        cursor += 2;
        continue;
      }
      if (char === '(') {
        depth += 1;
        destination += char;
        cursor += 1;
        continue;
      }
      if (char === ')') {
        if (depth === 0) break;
        depth -= 1;
        destination += char;
        cursor += 1;
        continue;
      }
      if (/\s/.test(char) && depth === 0) break;
      destination += char;
      cursor += 1;
    }
    if (destination) destinations.push(destination);
    index = cursor;
  }

  const definitionPattern = /^[ \t]{0,3}\[[^\]]+\]:(?:[ \t]*\r?\n)?[ \t]*(?:<([^>\n]+)>|([^\s]+))/gm;
  for (const match of source.matchAll(definitionPattern)) {
    const target = match[1] ?? match[2];
    if (target) destinations.push(unescapeMarkdownPunctuation(target.trim()));
  }

  return destinations;
}

function normalizeMarkdownTarget(target) {
  let value = target.trim();
  if (value.startsWith('<') && value.endsWith('>')) value = value.slice(1, -1);
  const queryIndex = value.indexOf('?');
  const hashIndex = value.indexOf('#');
  const cutIndex = [queryIndex, hashIndex].filter((index) => index >= 0).sort((a, b) => a - b)[0];
  return cutIndex === undefined ? value : value.slice(0, cutIndex);
}

export function validateLocalMarkdownTarget(skill, markdownFile, rawTarget, errors) {
  const target = normalizeMarkdownTarget(rawTarget);
  if (!target || target.startsWith('#')) return;
  const hasScheme = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(target);
  const isWindowsDrivePath = /^[a-z]:[\\/]/i.test(target);
  if (hasScheme && !isWindowsDrivePath) return;

  let decoded;
  try {
    decoded = decodeURIComponent(target);
  } catch {
    errors.push(`${toRepoRelative(markdownFile)}: invalid percent-encoding in local link "${rawTarget}"`);
    return;
  }

  if (path.isAbsolute(decoded) || path.win32.isAbsolute(decoded)) {
    errors.push(`${toRepoRelative(markdownFile)}: absolute local link is not portable: "${rawTarget}"`);
    return;
  }

  const platformNeutral = decoded.replace(/[\\/]+/g, path.sep);
  const resolved = path.resolve(path.dirname(markdownFile), platformNeutral);
  try {
    if (!isPathInsideRealRoot(skill.absDir, resolved)) {
      errors.push(`${toRepoRelative(markdownFile)}: local link escapes the current Skill: "${rawTarget}"`);
      return;
    }
  } catch (err) {
    errors.push(`${toRepoRelative(markdownFile)}: cannot resolve local link "${rawTarget}": ${err.message}`);
    return;
  }

  if (!fs.existsSync(resolved)) {
    errors.push(`${toRepoRelative(markdownFile)}: referenced local file does not exist: "${rawTarget}"`);
  }
}

const SKIPPED_MARKDOWN_SUBDIRS = new Set(['templates', 'tests']);

export function validateMarkdownLinks(skill, allFiles, errors) {
  const markdownFiles = allFiles.filter((filePath) => {
    if (!['.md', '.mdx'].includes(path.extname(filePath).toLowerCase())) return false;
    const topSubdir = path.relative(skill.absDir, filePath).split(path.sep)[0];
    return !SKIPPED_MARKDOWN_SUBDIRS.has(topSubdir);
  });
  for (const markdownFile of markdownFiles) {
    const text = readTextFile(markdownFile, errors);
    if (text === null) continue;

    for (const target of extractMarkdownDestinations(text)) {
      validateLocalMarkdownTarget(skill, markdownFile, target, errors);
    }
  }
}

function validateFrontmatters(skills, errors) {
  const seenNames = new Map();
  for (const skill of skills) {
    const relSkillMd = toRepoRelative(skill.skillMdPath);
    let content;
    try {
      content = fs.readFileSync(skill.skillMdPath, 'utf8');
    } catch (err) {
      errors.push(`${relSkillMd}: cannot read SKILL.md: ${err.message}`);
      continue;
    }

    const { data, error } = parseFrontmatter(content);
    if (error) {
      errors.push(`${relSkillMd}: ${error}`);
      continue;
    }

    if (typeof data.name !== 'string' || !data.name.trim()) {
      errors.push(`${relSkillMd}: frontmatter requires a non-empty string "name"`);
    } else {
      const name = data.name.trim();
      if (!KEBAB_CASE_RE.test(name)) {
        errors.push(`${relSkillMd}: name "${name}" must use lowercase kebab-case`);
      }
      if (name !== skill.skillDirName) {
        errors.push(`${relSkillMd}: name "${name}" does not match directory "${skill.skillDirName}"`);
      }
      if (!seenNames.has(name)) seenNames.set(name, []);
      seenNames.get(name).push(relSkillMd);
    }

    if (typeof data.description !== 'string' || !data.description.trim()) {
      errors.push(`${relSkillMd}: frontmatter requires a non-empty string "description"`);
    }
  }

  for (const [name, paths] of seenNames.entries()) {
    if (paths.length > 1) {
      errors.push(`Skill name "${name}" is duplicated: ${paths.join(', ')}`);
    }
  }
}

export function validateMarketplace(errors, expectedMarketplace, marketplacePath = MARKETPLACE_PATH) {
  const relManifest = toRepoRelative(marketplacePath);
  if (!fs.existsSync(marketplacePath)) {
    errors.push(`${relManifest}: generated marketplace is missing; run pnpm run skills:sync`);
    return;
  }

  let actual;
  try {
    actual = JSON.parse(fs.readFileSync(marketplacePath, 'utf8'));
  } catch (err) {
    errors.push(`${relManifest}: invalid JSON (${err.message})`);
    return;
  }

  if (!actual || typeof actual !== 'object' || Array.isArray(actual) || !Array.isArray(actual.plugins)) {
    errors.push(`${relManifest}: expected an object containing a plugins array`);
    return;
  }

  if (expectedMarketplace && !isDeepStrictEqual(actual, expectedMarketplace)) {
    const actualGroups = actual.plugins.map((plugin) => plugin?.name).filter(Boolean).sort();
    const expectedGroups = expectedMarketplace.plugins.map((plugin) => plugin.name);
    errors.push(
      `${relManifest}: generated file does not match manifest.json + skills/. ` +
      `Actual groups: [${actualGroups.join(', ')}]; expected groups: [${expectedGroups.join(', ')}]. ` +
      'Run node scripts/sync-marketplace.mjs to regenerate it.',
    );
  }
}

export function validateSkillContents(skills, errors) {
  for (const skill of skills) {
    const allFiles = collectFiles(skill.absDir, [], errors);
    validateMarkdownLinks(skill, allFiles, errors);
  }
}

export function validateRepository() {
  const { skills, errors } = scanSkillsTree(SKILLS_ROOT);
  let expectedMarketplace = null;
  try {
    const { manifestMeta, groups } = loadMarketplaceInfo();
    expectedMarketplace = createMarketplace(skills, manifestMeta, groups);
  } catch (err) {
    errors.push(err.message);
  }

  validateFrontmatters(skills, errors);
  validateMarketplace(errors, expectedMarketplace);
  validateSkillContents(skills, errors);
  return { skills, errors };
}

export function main() {
  const { skills, errors } = validateRepository();
  if (errors.length > 0) {
    console.error(`Validation failed: ${errors.length} issue(s).`);
    for (const error of errors) console.error(`  - ${error}`);
    return 1;
  }
  console.log(`Validation passed: ${skills.length} Skill(s) checked, 0 errors.`);
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
