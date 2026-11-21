import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { scanSkillsTree, SKILLS_ROOT } from './lib/skills.mjs';
import { normalizeScriptArgs } from './lib/cli-args.mjs';

export const AGENT_TARGET_PRESETS = {
  claude: path.join('.claude', 'skills'),
  'claude-code': path.join('.claude', 'skills'),
  agents: path.join('.agents', 'skills'),
  // Codex 同样加载 ~/.agents/skills/，指向同一目录避免冗余软链接
  codex: path.join('.agents', 'skills'),
  gemini: path.join('.gemini', 'skills'),
  antigravity: path.join('.gemini', 'skills'),
};

export function expandHome(inputPath) {
  if (inputPath === '~' || inputPath.startsWith(`~${path.sep}`) || inputPath.startsWith('~/')) {
    return path.join(os.homedir(), inputPath.slice(1).replace(/^[\\/]/, ''));
  }
  return path.resolve(inputPath);
}

export function parseArgs(argv) {
  const options = {
    targets: [],
    skillName: null,
    force: false,
    unlink: false,
    dryRun: false,
    help: false,
  };

  const args = [...argv];
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === '--help' || arg === '-h') {
      options.help = true;
    } else if (arg === '--force' || arg === '-f') {
      options.force = true;
    } else if (arg === '--unlink' || arg === '-u') {
      options.unlink = true;
    } else if (arg === '--dry-run' || arg === '-n') {
      options.dryRun = true;
    } else if (arg === '--target' || arg === '-t') {
      const next = args[i + 1];
      if (!next || next.startsWith('-')) throw new Error('Missing value for --target');
      options.targets.push(expandHome(next));
      i += 1;
    } else if (arg.startsWith('--target=')) {
      options.targets.push(expandHome(arg.slice('--target='.length)));
    } else if (arg === '--agent' || arg === '-a') {
      const next = args[i + 1];
      if (!next || next.startsWith('-')) throw new Error('Missing value for --agent');
      if (next === 'all') {
        const home = os.homedir();
        options.targets.push(path.join(home, AGENT_TARGET_PRESETS.claude));
        options.targets.push(path.join(home, AGENT_TARGET_PRESETS.agents));
        options.targets.push(path.join(home, AGENT_TARGET_PRESETS.gemini));
      } else if (AGENT_TARGET_PRESETS[next]) {
        options.targets.push(path.join(os.homedir(), AGENT_TARGET_PRESETS[next]));
      } else {
        throw new Error(`Unknown agent preset: "${next}". Supported: claude, claude-code, codex, agents, gemini, antigravity, all`);
      }
      i += 1;
    } else if (arg.startsWith('--agent=')) {
      const next = arg.slice('--agent='.length);
      if (next === 'all') {
        const home = os.homedir();
        options.targets.push(path.join(home, AGENT_TARGET_PRESETS.claude));
        options.targets.push(path.join(home, AGENT_TARGET_PRESETS.agents));
        options.targets.push(path.join(home, AGENT_TARGET_PRESETS.gemini));
      } else if (AGENT_TARGET_PRESETS[next]) {
        options.targets.push(path.join(os.homedir(), AGENT_TARGET_PRESETS[next]));
      } else {
        throw new Error(`Unknown agent preset: "${next}". Supported: claude, claude-code, codex, agents, gemini, antigravity, all`);
      }
    } else if (arg === '--skill' || arg === '-s') {
      const next = args[i + 1];
      if (!next || next.startsWith('-')) throw new Error('Missing value for --skill');
      options.skillName = next;
      i += 1;
    } else if (arg.startsWith('--skill=')) {
      options.skillName = arg.slice('--skill='.length);
    } else if (!arg.startsWith('-') && options.targets.length === 0) {
      options.targets.push(expandHome(arg));
    } else {
      throw new Error(`Unknown argument: "${arg}"`);
    }
  }

  options.targets = Array.from(new Set(options.targets));
  return options;
}

export function executeLinkOperation({
  targets,
  skillName,
  force = false,
  unlink = false,
  dryRun = false,
  skillsRoot = SKILLS_ROOT,
  logger = console,
}) {
  if (targets.length === 0) {
    logger.error('No target directory specified. Use --target <path> or --agent <claude|agents|codex|all>.');
    return 2;
  }

  const { skills, errors } = scanSkillsTree(skillsRoot);
  if (errors.length > 0) {
    logger.error('Errors found in skills catalog:');
    for (const error of errors) logger.error(`  - ${error}`);
    return 1;
  }

  const selectedSkills = skillName
    ? skills.filter((s) => s.skillDirName === skillName)
    : skills;

  if (skillName && selectedSkills.length === 0) {
    logger.error(`Skill "${skillName}" not found. Available: ${skills.map((s) => s.skillDirName).join(', ')}`);
    return 1;
  }

  const symlinkType = process.platform === 'win32' ? 'junction' : 'dir';
  let processedCount = 0;

  for (const targetDir of targets) {
    if (!unlink && !dryRun) {
      fs.mkdirSync(targetDir, { recursive: true });
    }

    for (const skill of selectedSkills) {
      const destPath = path.join(targetDir, skill.skillDirName);
      const destStat = fs.lstatSync(destPath, { throwIfNoEntry: false });

      if (unlink) {
        if (!destStat) {
          logger.log(`[skip] ${skill.skillDirName} not found in ${targetDir}`);
          continue;
        }
        if (destStat.isSymbolicLink()) {
          const rawTarget = fs.readlinkSync(destPath);
          const resolvedTarget = path.resolve(targetDir, rawTarget);
          if (resolvedTarget === path.resolve(skill.absDir) || force) {
            if (!dryRun) fs.unlinkSync(destPath);
            logger.log(`[unlinked] ${skill.skillDirName} from ${targetDir}`);
            processedCount += 1;
          } else {
            logger.warn(`[skip] ${destPath} links to ${resolvedTarget} (not this skill). Use --force to remove.`);
          }
        } else {
          logger.warn(`[skip] ${destPath} is a regular directory/file, not a symlink. Use manual removal.`);
        }
        continue;
      }

      // Link mode
      if (!destStat) {
        if (!dryRun) fs.symlinkSync(skill.absDir, destPath, symlinkType);
        logger.log(`[linked] ${skill.skillDirName} -> ${destPath}`);
        processedCount += 1;
        continue;
      }

      if (destStat.isSymbolicLink()) {
        const rawTarget = fs.readlinkSync(destPath);
        const resolvedTarget = path.resolve(targetDir, rawTarget);
        if (resolvedTarget === path.resolve(skill.absDir)) {
          logger.log(`[already-linked] ${skill.skillDirName} in ${targetDir}`);
          continue;
        }
        if (force) {
          if (!dryRun) {
            fs.unlinkSync(destPath);
            fs.symlinkSync(skill.absDir, destPath, symlinkType);
          }
          logger.log(`[re-linked] ${skill.skillDirName} -> ${destPath} (overwrote previous symlink)`);
          processedCount += 1;
        } else {
          logger.warn(`[conflict] ${destPath} links to ${resolvedTarget}. Use --force to replace.`);
        }
        continue;
      }

      // Regular file or directory exists
      if (force) {
        if (!dryRun) {
          fs.rmSync(destPath, { recursive: true, force: true });
          fs.symlinkSync(skill.absDir, destPath, symlinkType);
        }
        logger.log(`[overwritten] ${skill.skillDirName} -> ${destPath} (replaced regular file/dir)`);
        processedCount += 1;
      } else {
        logger.warn(`[conflict] ${destPath} already exists and is not a symlink. Use --force to replace.`);
      }
    }
  }

  logger.log(`Done. Processed ${processedCount} item(s).`);
  return 0;
}

export function printHelp() {
  console.log(`
Usage: pnpm run skills:link [options] [target-directory]

Link workspace Skills into local Agent skill directories for development.

Options:
  -t, --target <path>     Target directory (e.g. ~/.claude/skills, ~/.agents/skills, ~/.gemini/skills)
  -a, --agent <name>      Preset agent target: claude, claude-code, codex, agents, gemini, antigravity, all
                          (codex resolves to ~/.agents/skills; gemini/antigravity resolves to ~/.gemini/skills; all targets claude, agents, and gemini)
  -s, --skill <name>      Only link a specific Skill by name (default: all)
  -f, --force             Overwrite existing symlinks or conflicting directories
  -u, --unlink            Remove symlinks created for workspace Skills
  -n, --dry-run           Preview actions without writing to disk
  -h, --help              Show this help message

Examples:
  pnpm run skills:link --target ~/.claude/skills
  pnpm run skills:link --agent claude
  pnpm run skills:link --agent gemini
  pnpm run skills:link --agent agents --skill git-commit-message
  pnpm run skills:link --agent all
  pnpm run skills:link --agent claude --unlink
`.trim());
}

export function main(argv = process.argv.slice(2)) {
  let options;
  try {
    options = parseArgs(normalizeScriptArgs(argv));
  } catch (err) {
    console.error(`Error: ${err.message}`);
    console.error('Run "pnpm run skills:link --help" for usage.');
    return 2;
  }

  if (options.help) {
    printHelp();
    return 0;
  }

  return executeLinkOperation(options);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
