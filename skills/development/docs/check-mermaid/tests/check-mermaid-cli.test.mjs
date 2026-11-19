import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const skillDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const scriptPath = path.join(skillDir, 'scripts/check_mermaid.ts');
const nodeArgs = ['--experimental-strip-types', scriptPath];

function run(args, env = process.env) {
  return spawnSync(process.execPath, [...nodeArgs, ...args], { encoding: 'utf8', env });
}

function parseOutput(result) {
  assert.ok(result.stdout.trim(), `Expected JSON stdout. stderr: ${result.stderr}`);
  return JSON.parse(result.stdout);
}

function makeFakeMmdc(t) {
  const binDir = fs.mkdtempSync(path.join(os.tmpdir(), 'fake-mmdc-'));
  t.after(() => fs.rmSync(binDir, { recursive: true, force: true }));
  const commandPath = path.join(binDir, 'mmdc');
  fs.writeFileSync(commandPath, `#!/bin/sh\ninput=''\noutput=''\nwhile [ "$#" -gt 0 ]; do\n  case "$1" in\n    -i) input="$2"; shift 2 ;;\n    -o) output="$2"; shift 2 ;;\n    *) shift ;;\n  esac\ndone\nif grep -q INVALID "$input"; then\n  echo 'Parse error on line 1' >&2\n  exit 1\nfi\nprintf '<svg></svg>' > "$output"\n`, 'utf8');
  fs.chmodSync(commandPath, 0o755);
  return `${binDir}:${process.env.PATH ?? ''}`;
}

test('file without Mermaid blocks succeeds without a renderer', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mermaid-no-block-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'plain.md');
  fs.writeFileSync(file, '# Plain document\n');
  const result = run([file], { ...process.env, PATH: dir });
  assert.equal(result.status, 0);
  assert.equal(parseOutput(result).total, 0);
});

test('valid diagram is rendered and line numbers point to content', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mermaid-valid-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'diagram.md');
  fs.writeFileSync(file, 'Intro\n```mermaid\nflowchart TD\nA --> B\n```\n');
  const result = run([file], { ...process.env, PATH: makeFakeMmdc(t) });
  assert.equal(result.status, 0, result.stderr);
  const json = parseOutput(result);
  assert.equal(json.blocks[0].start_line, 3);
  assert.equal(json.blocks[0].end_line, 5);
  assert.equal(json.blocks[0].ok, true);
});

test('syntax failure is classified separately from environment failure', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mermaid-invalid-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'diagram.md');
  fs.writeFileSync(file, '```mermaid\nINVALID\n```\n');
  const result = run([file], { ...process.env, PATH: makeFakeMmdc(t) });
  assert.equal(result.status, 1);
  assert.equal(parseOutput(result).blocks[0].error_type, 'syntax_error');
});

test('unknown flags and extra positional arguments are rejected', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mermaid-args-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'diagram.md');
  fs.writeFileSync(file, '# test');
  const unknown = run([file, '--keep-tempp']);
  assert.equal(unknown.status, 2);
  assert.equal(parseOutput(unknown).error_type, 'input_error');
  const extra = run([file, path.join(dir, 'other.md')]);
  assert.equal(extra.status, 2);
  assert.match(parseOutput(extra).error, /extra positional/);
});

test('--keep-temp reports the directory and generated file paths', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mermaid-keep-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'diagram.md');
  fs.writeFileSync(file, '```mermaid\nflowchart TD\nA --> B\n```\n');
  const result = run([file, '--keep-temp'], { ...process.env, PATH: makeFakeMmdc(t) });
  assert.equal(result.status, 0);
  const json = parseOutput(result);
  assert.ok(fs.existsSync(json.temp_dir));
  assert.ok(fs.existsSync(json.blocks[0].input_file));
  assert.ok(fs.existsSync(json.blocks[0].output_file));
});
