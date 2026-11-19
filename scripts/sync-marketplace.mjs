import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { MARKETPLACE_PATH, buildMarketplace, toRepoRelative } from './lib/skills.mjs';
import { normalizeScriptArgs } from './lib/cli-args.mjs';

function parseArgs(argv) {
  const options = { check: false, help: false };
  for (const arg of argv) {
    if (arg === '--check') options.check = true;
    else if (arg === '--help' || arg === '-h') options.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return options;
}

function serialize(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function writeAtomically(filePath, content) {
  const directory = path.dirname(filePath);
  fs.mkdirSync(directory, { recursive: true });
  const temporaryPath = path.join(
    directory,
    `.${path.basename(filePath)}.${process.pid}.${Date.now()}.tmp`,
  );

  try {
    const fd = fs.openSync(temporaryPath, 'wx', 0o644);
    try {
      fs.writeFileSync(fd, content, 'utf8');
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(temporaryPath, filePath);
  } catch (err) {
    try {
      fs.rmSync(temporaryPath, { force: true });
    } catch {
      // Preserve the original write error.
    }
    throw err;
  }
}

export function main(argv = process.argv.slice(2)) {
  let options;
  try {
    options = parseArgs(normalizeScriptArgs(argv));
  } catch (err) {
    console.error(err.message);
    console.error('Usage: pnpm run skills:sync [-- --check]');
    return 2;
  }

  if (options.help) {
    console.log('Usage: pnpm run skills:sync [-- --check]');
    console.log('Default: regenerate marketplace.json. --check: verify without writing.');
    return 0;
  }

  const { skills, marketplace, errors } = buildMarketplace();
  if (errors.length > 0) {
    console.error('Marketplace generation failed:');
    for (const error of errors) console.error(`  - ${error}`);
    return 1;
  }

  const expected = serialize(marketplace);
  const relPath = toRepoRelative(MARKETPLACE_PATH);

  if (options.check) {
    if (!fs.existsSync(MARKETPLACE_PATH)) {
      console.error(`${relPath} is missing. Run pnpm run skills:sync.`);
      return 1;
    }
    const current = fs.readFileSync(MARKETPLACE_PATH, 'utf8');
    if (current !== expected) {
      console.error(`${relPath} is out of date. Run pnpm run skills:sync.`);
      return 1;
    }
    console.log(`${relPath} is up to date (${skills.length} Skill(s), ${marketplace.plugins.length} group(s)).`);
    return 0;
  }

  try {
    writeAtomically(MARKETPLACE_PATH, expected);
  } catch (err) {
    console.error(`Cannot write ${relPath}: ${err.message}`);
    return 1;
  }

  console.log(`Synced ${skills.length} Skill(s) across ${marketplace.plugins.length} Plugin group(s) -> ${relPath}`);
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main();
}
