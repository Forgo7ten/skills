import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseDocument } from 'yaml';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export const REPO_ROOT = path.resolve(__dirname, '../..');
export const SKILLS_ROOT = path.join(REPO_ROOT, 'skills');
export const MARKETPLACE_PATH = path.join(REPO_ROOT, '.claude-plugin', 'marketplace.json');
export const INFO_CONFIG_PATH = path.join(REPO_ROOT, 'manifest.json');

export const IGNORED_DIRS = new Set([
  'node_modules',
  '.git',
  'dist',
  'build',
  '__pycache__',
]);

export const ALLOWED_CATEGORY_FILES = new Set([
  'README.md',
  '.gitkeep',
  '.DS_Store',
]);

export const RESOURCE_DIRS = new Set(['references', 'scripts', 'assets']);
export const KEBAB_CASE_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/**
 * Locale-independent string comparator for reproducible ordering.
 * @param {string} a
 * @param {string} b
 * @returns {number}
 */
export function compareStrings(a, b) {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

/**
 * Convert an absolute path to a POSIX-style path relative to REPO_ROOT.
 * @param {string} absPath
 * @returns {string}
 */
export function toRepoRelative(absPath) {
  return path.relative(REPO_ROOT, absPath).split(path.sep).join('/');
}

/**
 * Resolve a path to its physical realpath. If the target does not exist on disk,
 * resolve its nearest existing ancestor with realpathSync and re-append the tail.
 * @param {string} targetPath
 * @returns {string}
 */
export function toRealPath(targetPath) {
  const resolved = path.resolve(targetPath);
  const tail = [];
  let probe = resolved;

  while (!fs.existsSync(probe)) {
    const parent = path.dirname(probe);
    if (parent === probe) return resolved;
    tail.unshift(path.basename(probe));
    probe = parent;
  }

  const realBase = fs.realpathSync(probe);
  return tail.length > 0 ? path.join(realBase, ...tail) : realBase;
}

/**
 * Check whether candidatePath stays within rootDir using resolved realpaths.
 * @param {string} rootDir
 * @param {string} candidatePath
 * @returns {boolean}
 */
export function isPathInsideRealRoot(rootDir, candidatePath) {
  const realRoot = toRealPath(rootDir);
  const realCandidate = toRealPath(candidatePath);
  const rel = path.relative(realRoot, realCandidate);
  return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel));
}

function isPlainObject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

/**
 * Load and validate the repository source manifest. marketplace.json is deliberately
 * not read here: it is a generated artifact, never an input to generation.
 * @param {string} [configPath=INFO_CONFIG_PATH]
 * @returns {{ manifestMeta: Record<string, unknown>, groups: Record<string, string> }}
 */
export function loadMarketplaceInfo(configPath = INFO_CONFIG_PATH) {
  const relPath = toRepoRelative(configPath) || 'manifest.json';
  if (!fs.existsSync(configPath)) {
    throw new Error(`Source manifest ${relPath} does not exist`);
  }

  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  } catch (err) {
    throw new Error(`Cannot parse ${relPath}: ${err.message}`);
  }

  if (!isPlainObject(parsed)) {
    throw new Error(`${relPath} must contain a JSON object`);
  }
  if (typeof parsed.name !== 'string' || !parsed.name.trim()) {
    throw new Error(`${relPath} requires a non-empty string "name"`);
  }

  if (parsed.owner !== undefined) {
    if (!isPlainObject(parsed.owner)) {
      throw new Error(`${relPath}: "owner" must be an object`);
    }
    if (parsed.owner.name !== undefined && (typeof parsed.owner.name !== 'string' || !parsed.owner.name.trim())) {
      throw new Error(`${relPath}: owner.name must be a non-empty string when provided`);
    }
  }

  if (parsed.metadata !== undefined) {
    if (!isPlainObject(parsed.metadata)) {
      throw new Error(`${relPath}: "metadata" must be an object`);
    }
    if (parsed.metadata.description !== undefined && typeof parsed.metadata.description !== 'string') {
      throw new Error(`${relPath}: metadata.description must be a string`);
    }
  }

  const groups = Object.create(null);
  if (parsed.groups !== undefined) {
    if (!isPlainObject(parsed.groups)) {
      throw new Error(`${relPath}: "groups" must be an object mapping group IDs to descriptions`);
    }
    for (const [key, value] of Object.entries(parsed.groups)) {
      if (!KEBAB_CASE_RE.test(key)) {
        throw new Error(`${relPath}: group ID "${key}" must use lowercase kebab-case`);
      }
      if (typeof value !== 'string' || !value.trim()) {
        throw new Error(`${relPath}: groups["${key}"] must be a non-empty string`);
      }
      groups[key] = value.trim();
    }
  }

  const { groups: _groups, plugins: _plugins, ...manifestMeta } = parsed;
  return { manifestMeta, groups };
}

/**
 * Check whether a directory entry should be skipped during traversal.
 * @param {string} name
 * @returns {boolean}
 */
export function isIgnoredEntry(name) {
  return IGNORED_DIRS.has(name) || name.startsWith('.');
}

/**
 * Recursively find any nested SKILL.md inside a Skill's subdirectories.
 * @param {string} dir
 * @param {string[]} hits
 */
function findNestedSkillMd(dir, hits, errors) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (err) {
    errors.push(`${toRepoRelative(dir)}: cannot read directory: ${err.message}`);
    return;
  }
  for (const entry of entries) {
    if (isIgnoredEntry(entry.name)) continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      findNestedSkillMd(fullPath, hits, errors);
    } else if (entry.isFile() && entry.name === 'SKILL.md') {
      hits.push(fullPath);
    }
  }
}

/**
 * Inspect entries inside a non-skill directory.
 * @param {string} dir
 * @param {string} levelLabel
 * @param {string[]} errors
 * @returns {fs.Dirent[]} Valid child directories
 */
function readCategoryDir(dir, levelLabel, errors) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (err) {
    errors.push(`${toRepoRelative(dir)}: cannot read ${levelLabel}: ${err.message}`);
    return [];
  }

  const subdirs = [];
  for (const entry of entries) {
    if (IGNORED_DIRS.has(entry.name)) continue;

    const fullPath = path.join(dir, entry.name);
    const relPath = toRepoRelative(fullPath);

    if (entry.isSymbolicLink()) {
      errors.push(`${relPath}: symbolic links are not allowed in the Skill catalog tree`);
      continue;
    }

    if (entry.isDirectory()) {
      if (entry.name.startsWith('.')) continue;
      if (RESOURCE_DIRS.has(entry.name)) {
        errors.push(`${relPath}: resource directory ${entry.name}/ must be inside a Skill directory`);
        continue;
      }
      subdirs.push(entry);
      continue;
    }

    if (entry.isFile()) {
      if (entry.name === 'SKILL.md') {
        errors.push(`${relPath}: ${levelLabel} cannot contain SKILL.md directly; place it inside a Skill directory`);
        continue;
      }
      if (!ALLOWED_CATEGORY_FILES.has(entry.name) && !entry.name.startsWith('.')) {
        errors.push(`${relPath}: unexpected file in ${levelLabel}; only Skill directories and README.md are allowed`);
      }
    }
  }

  return subdirs.sort((a, b) => compareStrings(a.name, b.name));
}

function inspectSkillDirectory(
  { category, subcategory, skillDirName, groupName, absDir, skillMdPath, manifestPath },
  errors,
  skills,
) {
  if (!KEBAB_CASE_RE.test(skillDirName)) {
    errors.push(`${toRepoRelative(absDir)}: Skill directory name must use lowercase kebab-case`);
  }

  let stat;
  try {
    stat = fs.statSync(skillMdPath);
  } catch {
    errors.push(`${toRepoRelative(absDir)}: Skill directory is missing SKILL.md`);
    return;
  }
  if (!stat.isFile()) {
    errors.push(`${toRepoRelative(skillMdPath)}: SKILL.md is not a regular file`);
    return;
  }

  const nestedHits = [];
  let innerEntries = [];
  try {
    innerEntries = fs.readdirSync(absDir, { withFileTypes: true });
  } catch (err) {
    errors.push(`${toRepoRelative(absDir)}: cannot read Skill directory: ${err.message}`);
    return;
  }
  for (const inner of innerEntries) {
    if (inner.isDirectory() && !isIgnoredEntry(inner.name)) {
      findNestedSkillMd(path.join(absDir, inner.name), nestedHits, errors);
    }
    if (inner.isSymbolicLink()) {
      errors.push(`${toRepoRelative(path.join(absDir, inner.name))}: symbolic links are not allowed inside a Skill`);
    }
  }
  for (const hit of nestedHits) {
    errors.push(`${toRepoRelative(hit)}: nested independent Skills are not allowed inside ${toRepoRelative(absDir)}`);
  }

  skills.push({
    category,
    subcategory,
    skillDirName,
    groupName,
    absDir,
    skillMdPath,
    manifestPath,
  });
}

/**
 * Scan the skills/ tree according to:
 * - Two-level catalog: skills/<category>/<skill-name>/SKILL.md
 * - Three-level catalog: skills/<category>/<subcategory>/<skill-name>/SKILL.md
 * @param {string} [skillsRoot=SKILLS_ROOT]
 * @returns {{ skills: Array<object>, errors: string[] }}
 */
export function scanSkillsTree(skillsRoot = SKILLS_ROOT) {
  const skills = [];
  const errors = [];

  if (!fs.existsSync(skillsRoot)) {
    errors.push(`${toRepoRelative(skillsRoot) || 'skills'}: skills directory does not exist`);
    return { skills, errors };
  }
  if (!fs.statSync(skillsRoot).isDirectory()) {
    errors.push(`${toRepoRelative(skillsRoot) || 'skills'}: skills path is not a directory`);
    return { skills, errors };
  }

  const categories = readCategoryDir(skillsRoot, 'skills root', errors);
  const groupOwners = new Map();

  for (const catEntry of categories) {
    const category = catEntry.name;
    const catDir = path.join(skillsRoot, category);
    if (!KEBAB_CASE_RE.test(category)) {
      errors.push(`${toRepoRelative(catDir)}: category name must use lowercase kebab-case`);
    }
    const childEntries = readCategoryDir(catDir, `category ${category}`, errors);

    for (const childEntry of childEntries) {
      const childName = childEntry.name;
      const childAbsDir = path.join(catDir, childName);
      const childSkillMd = path.join(childAbsDir, 'SKILL.md');

      if (fs.existsSync(childSkillMd)) {
        // Two-level catalog: skills/<category>/<skill-name>/SKILL.md
        const groupName = category;
        const groupPath = category;
        const previousGroupPath = groupOwners.get(groupName);
        if (previousGroupPath && previousGroupPath !== groupPath) {
          errors.push(`Plugin group ID collision: "${groupName}" is generated by both "${previousGroupPath}" and "${groupPath}"`);
        } else {
          groupOwners.set(groupName, groupPath);
        }

        inspectSkillDirectory(
          {
            category,
            subcategory: null,
            skillDirName: childName,
            groupName,
            absDir: childAbsDir,
            skillMdPath: childSkillMd,
            manifestPath: `./skills/${category}/${childName}`,
          },
          errors,
          skills,
        );
      } else {
        // Three-level catalog: skills/<category>/<subcategory>/<skill-name>/SKILL.md
        const subcategory = childName;
        const subDir = childAbsDir;
        if (!KEBAB_CASE_RE.test(subcategory)) {
          errors.push(`${toRepoRelative(subDir)}: subcategory name must use lowercase kebab-case`);
        }
        const skillEntries = readCategoryDir(subDir, `subcategory ${category}/${subcategory}`, errors);
        const groupName = `${category}-${subcategory}`;
        const groupPath = `${category}/${subcategory}`;
        const previousGroupPath = groupOwners.get(groupName);
        if (previousGroupPath && previousGroupPath !== groupPath) {
          errors.push(`Plugin group ID collision: "${groupName}" is generated by both "${previousGroupPath}" and "${groupPath}"`);
        } else {
          groupOwners.set(groupName, groupPath);
        }

        for (const skillEntry of skillEntries) {
          const skillDirName = skillEntry.name;
          const absDir = path.join(subDir, skillDirName);
          const skillMdPath = path.join(absDir, 'SKILL.md');

          inspectSkillDirectory(
            {
              category,
              subcategory,
              skillDirName,
              groupName,
              absDir,
              skillMdPath,
              manifestPath: `./skills/${category}/${subcategory}/${skillDirName}`,
            },
            errors,
            skills,
          );
        }
      }
    }
  }

  skills.sort((a, b) => compareStrings(a.manifestPath, b.manifestPath));
  return { skills, errors };
}

/**
 * Parse YAML frontmatter using the standard YAML 1.2 parser.
 * Duplicate keys and malformed YAML are errors rather than silently taking a later value.
 * @param {string} content
 * @returns {{ data: Record<string, unknown>, error: string | null }}
 */
export function parseFrontmatter(content) {
  const normalized = content.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
  const opening = /^---[ \t]*\n/.exec(normalized);
  if (!opening) {
    return { data: {}, error: 'missing YAML frontmatter opening delimiter (---)' };
  }

  const bodyStart = opening[0].length;
  const rest = normalized.slice(bodyStart);
  const closingMatch = /^(?:---|\.\.\.)[ \t]*(?:\n|$)/m.exec(rest);
  if (!closingMatch) {
    return { data: {}, error: 'missing YAML frontmatter closing delimiter (---)' };
  }

  const rawBlock = rest.slice(0, closingMatch.index);
  try {
    const document = parseDocument(rawBlock, {
      uniqueKeys: true,
      prettyErrors: false,
      strict: true,
    });
    if (document.errors.length > 0) {
      return {
        data: {},
        error: document.errors.map((item) => item.message).join('; '),
      };
    }
    const data = document.toJS({ mapAsMap: false });
    if (data === null || data === undefined) return { data: {}, error: null };
    if (!isPlainObject(data)) {
      return { data: {}, error: 'YAML frontmatter must be a mapping/object' };
    }
    return { data, error: null };
  } catch (err) {
    return { data: {}, error: `YAML parse failed: ${err.message}` };
  }
}

/**
 * Build the exact marketplace object from source manifest metadata and discovered Skills.
 * This pure transformation is shared by sync and validation.
 * @param {Array<object>} skills
 * @param {Record<string, unknown>} manifestMeta
 * @param {Record<string, string>} groups
 * @returns {Record<string, unknown>}
 */
export function createMarketplace(skills, manifestMeta, groups) {
  const grouped = new Map();
  const seenPaths = new Set();

  for (const skill of skills) {
    if (seenPaths.has(skill.manifestPath)) {
      throw new Error(`Duplicate Skill path discovered: ${skill.manifestPath}`);
    }
    seenPaths.add(skill.manifestPath);

    if (!grouped.has(skill.groupName)) {
      const fallbackDesc = skill.subcategory
        ? `${skill.category} ${skill.subcategory} skills`
        : `${skill.category} skills`;
      grouped.set(skill.groupName, {
        name: skill.groupName,
        source: './',
        description: groups[skill.groupName] || fallbackDesc,
        skills: [],
      });
    }
    grouped.get(skill.groupName).skills.push(skill.manifestPath);
  }

  const plugins = Array.from(grouped.values())
    .sort((a, b) => compareStrings(a.name, b.name))
    .map((plugin) => ({
      ...plugin,
      skills: [...plugin.skills].sort(compareStrings),
    }));

  return { ...manifestMeta, plugins };
}

/**
 * Discover Skills and build the marketplace object from the two source inputs.
 * @param {{ skillsRoot?: string, configPath?: string }} [options]
 * @returns {{ skills: Array<object>, marketplace: Record<string, unknown>, errors: string[] }}
 */
export function buildMarketplace(options = {}) {
  const { skillsRoot = SKILLS_ROOT, configPath = INFO_CONFIG_PATH } = options;
  const { skills, errors } = scanSkillsTree(skillsRoot);
  if (errors.length > 0) return { skills, marketplace: {}, errors };

  try {
    const { manifestMeta, groups } = loadMarketplaceInfo(configPath);
    return { skills, marketplace: createMarketplace(skills, manifestMeta, groups), errors: [] };
  } catch (err) {
    return { skills, marketplace: {}, errors: [err.message] };
  }
}
