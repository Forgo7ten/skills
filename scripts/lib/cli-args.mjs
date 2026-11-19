/**
 * Remove the optional argument separator forwarded by package managers.
 *
 * For example, pnpm may invoke a script with ['--', '--check'] for:
 *   pnpm run skills:sync -- --check
 *
 * Only a leading separator is special. A later '--' remains an ordinary
 * argument and is rejected by the command-specific parser unless supported.
 * The input array is never mutated.
 */
export function normalizeScriptArgs(argv) {
  const args = [...argv];
  if (args[0] === '--') args.shift();
  return args;
}
