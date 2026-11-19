#!/usr/bin/env -S npx tsx
/**
 * check_mermaid.ts — 校验 Markdown 文件中所有 Mermaid 围栏代码块。
 *
 * 用法:
 *   npx --yes tsx check_mermaid.ts <markdown_file> [--keep-temp]
 *
 * 渲染器选择策略（保持不变）:
 *   1) 优先使用 PATH 中已安装的 `mmdc`
 *   2) 否则回退到 `npx --yes --package @mermaid-js/mermaid-cli mmdc`
 *
 * 输出（stdout JSON）:
 *   {
 *     "file": "<input>",
 *     "runner": "mmdc" | "npx" | "none",
 *     "total": number,
 *     "passed": number,
 *     "failed": number,
 *     "blocks": [
 *       { index, start_line, end_line, ok, diagram_type, error_type?, error?, snippet?, input_file?, output_file? }
 *     ],
 *     "temp_dir"?: string
 *   }
 *
 * 退出码:
 *   0  全部通过，或文档中不含 Mermaid 代码块
 *   1  存在 Mermaid 语法或渲染失败块
 *   2  输入参数、围栏结构、运行环境、依赖拉取或超时错误
 */

import { spawnSync } from 'node:child_process';
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { extractMermaidBlocks } from './lib/markdown-fences.mjs';

type ErrorType =
  | 'syntax_error'
  | 'render_error'
  | 'environment_error'
  | 'dependency_error'
  | 'input_error'
  | 'timeout';

type MermaidBlock = {
  index: number;
  start_line: number;
  end_line: number;
  ok: boolean;
  diagram_type: string;
  error_type?: ErrorType;
  error?: string;
  snippet?: string;
  input_file?: string;
  output_file?: string;
};

type CheckResult = {
  file: string;
  runner: 'mmdc' | 'npx' | 'none';
  total: number;
  passed: number;
  failed: number;
  blocks: MermaidBlock[];
  temp_dir?: string;
  error_type?: ErrorType;
  error?: string;
};

const DIAGRAM_TYPES = [
  'flowchart', 'graph', 'sequenceDiagram', 'classDiagram',
  'stateDiagram-v2', 'stateDiagram', 'erDiagram', 'journey',
  'gantt', 'pie', 'mindmap', 'timeline', 'gitGraph', 'C4Context',
];

function detectDiagramType(code: string): string {
  for (const raw of code.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('%%')) continue;
    for (const type of DIAGRAM_TYPES) {
      if (line.startsWith(type)) return type;
    }
    return 'unknown';
  }
  return 'unknown';
}

function commandExists(cmd: string): boolean {
  const r = spawnSync(process.platform === 'win32' ? 'where' : 'which', [cmd], { encoding: 'utf-8' });
  return r.status === 0 && (r.stdout || '').trim().length > 0;
}

type Runner = { type: 'mmdc' | 'npx' | 'none'; cmd: string; argsPrefix: string[] };

function pickRunner(): Runner {
  if (commandExists('mmdc')) {
    return { type: 'mmdc', cmd: 'mmdc', argsPrefix: [] };
  }
  if (commandExists('npx')) {
    return {
      type: 'npx',
      cmd: 'npx',
      argsPrefix: ['--yes', '--package', '@mermaid-js/mermaid-cli', 'mmdc'],
    };
  }
  return { type: 'none', cmd: '', argsPrefix: [] };
}

function classifyRenderError(output: string, spawnError: NodeJS.ErrnoException | undefined, runner: Runner['type']): ErrorType {
  if (spawnError?.code === 'ETIMEDOUT') return 'timeout';
  if (/parse error on line|lexical error|unknownDiagramError|no diagram type detected|syntax error/i.test(output)) {
    return 'syntax_error';
  }
  if (runner === 'npx' && /npm (?:ERR!|error)|EAI_AGAIN|ENOTFOUND|ECONN(?:RESET|REFUSED|TIMEDOUT)|fetch failed|network error|no matching version found/i.test(output)) {
    return 'dependency_error';
  }
  if (/could not find (?:chrome|chromium)|failed to launch|failed to start browser|browser.*not found|executable.*does not exist|sandbox.*denied/i.test(output)) {
    return 'environment_error';
  }
  if (spawnError?.code === 'ENOENT' || spawnError?.code === 'EACCES') return 'environment_error';
  return 'render_error';
}

function renderOne(
  runner: Runner,
  code: string,
  workdir: string,
  index: number,
  keepTemp: boolean,
): { ok: boolean; error_type?: ErrorType; error?: string; input_file: string; output_file: string } {
  const inPath = path.join(workdir, `mermaid-${index}.mmd`);
  const outPath = path.join(workdir, `mermaid-${index}.svg`);
  try {
    writeFileSync(inPath, code.endsWith('\n') ? code : `${code}\n`, 'utf-8');
    const args = [...runner.argsPrefix, '-i', inPath, '-o', outPath, '-q'];
    const r = spawnSync(runner.cmd, args, { encoding: 'utf-8', timeout: 90_000 });
    const output = [r.stderr, r.stdout].filter(Boolean).join('\n').trim();
    const ok = r.status === 0 && existsSync(outPath);
    if (ok) return { ok: true, input_file: inPath, output_file: outPath };

    let error = output;
    if (r.error) error = `${r.error.message}${error ? `\n${error}` : ''}`;
    if (!error) error = `mmdc exited with status ${String(r.status)}`;
    if (error.length > 2000) error = `${error.slice(0, 2000)}\n...[truncated]`;

    return {
      ok: false,
      error_type: classifyRenderError(output, r.error, runner.type),
      error,
      input_file: inPath,
      output_file: outPath,
    };
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      error_type: 'environment_error',
      error,
      input_file: inPath,
      output_file: outPath,
    };
  } finally {
    if (!keepTemp) {
      try { rmSync(inPath, { force: true }); } catch { /* best-effort cleanup */ }
      try { rmSync(outPath, { force: true }); } catch { /* best-effort cleanup */ }
    }
  }
}

function firstLines(code: string, count = 5): string {
  return code.split(/\r?\n/).slice(0, count).join('\n');
}

function parseArgs(argv: string[]): { mdPath?: string; keepTemp: boolean; help: boolean; error?: string } {
  const result = { mdPath: undefined as string | undefined, keepTemp: false, help: false, error: undefined as string | undefined };
  let positionalOnly = false;

  for (const arg of argv) {
    if (!positionalOnly && arg === '--') {
      positionalOnly = true;
    } else if (!positionalOnly && (arg === '--help' || arg === '-h')) {
      result.help = true;
    } else if (!positionalOnly && arg === '--keep-temp') {
      result.keepTemp = true;
    } else if (!positionalOnly && arg.startsWith('-')) {
      result.error = `Unknown option: ${arg}`;
      return result;
    } else if (!result.mdPath) {
      result.mdPath = arg;
    } else {
      result.error = `Unexpected extra positional argument: ${arg}`;
      return result;
    }
  }
  return result;
}

function emitTopLevelError(error_type: ErrorType, error: string, file?: string): number {
  console.log(JSON.stringify({ ...(file ? { file } : {}), error_type, error }, null, 2));
  return 2;
}

function run(): number {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.error('Usage: npx --yes tsx check_mermaid.ts <markdown_file> [--keep-temp]');
    return 0;
  }
  if (args.error) return emitTopLevelError('input_error', args.error);
  if (!args.mdPath) return emitTopLevelError('input_error', 'Missing Markdown file path');

  const mdPath = path.resolve(args.mdPath);
  let inputStat;
  try {
    inputStat = statSync(mdPath);
  } catch {
    inputStat = undefined;
  }
  if (!existsSync(mdPath)) return emitTopLevelError('input_error', `File not found: ${mdPath}`, mdPath);

  let md;
  try {
    if (inputStat && !inputStat.isFile()) return emitTopLevelError('input_error', `Input is not a regular file: ${mdPath}`, mdPath);
    md = readFileSync(mdPath, 'utf-8');
  } catch (err) {
    return emitTopLevelError('input_error', `Cannot read input file: ${err instanceof Error ? err.message : String(err)}`, mdPath);
  }

  const blocks = extractMermaidBlocks(md);
  const result: CheckResult = {
    file: mdPath,
    runner: 'none',
    total: blocks.length,
    passed: 0,
    failed: 0,
    blocks: [],
  };

  // 若 Markdown 不包含任何 Mermaid 代码块，则无需调用 mmdc 或 npx
  if (blocks.length === 0) {
    console.log(JSON.stringify(result, null, 2));
    return 0;
  }

  const renderableBlocks = blocks.filter((block) => !block.structure_error);
  const runner = renderableBlocks.length > 0 ? pickRunner() : { type: 'none' as const, cmd: '', argsPrefix: [] };
  result.runner = runner.type;

  let workdir: string | undefined;
  if (renderableBlocks.length > 0 && runner.type !== 'none') {
    try {
      workdir = mkdtempSync(path.join(tmpdir(), 'check-mermaid-'));
      if (args.keepTemp) result.temp_dir = workdir;
    } catch (err) {
      return emitTopLevelError('environment_error', `Cannot create temporary directory: ${err instanceof Error ? err.message : String(err)}`, mdPath);
    }
  }

  let hasInfrastructureFailure = false;
  try {
    blocks.forEach((block, index) => {
      const diagramType = detectDiagramType(block.code);
      const output: MermaidBlock = {
        index: index + 1,
        start_line: block.start_line,
        end_line: block.end_line,
        ok: false,
        diagram_type: diagramType,
      };

      if (block.structure_error) {
        output.error_type = 'input_error';
        output.error = block.structure_error;
        output.snippet = firstLines(block.code);
      } else if (runner.type === 'none') {
        output.error_type = 'environment_error';
        output.error = 'Neither mmdc nor npx is available in PATH.';
        output.snippet = firstLines(block.code);
      } else {
        const rendered = renderOne(runner, block.code, workdir!, index + 1, args.keepTemp);
        output.ok = rendered.ok;
        if (!rendered.ok) {
          output.error_type = rendered.error_type;
          output.error = rendered.error;
          output.snippet = firstLines(block.code);
          if (rendered.error_type === 'environment_error' || rendered.error_type === 'dependency_error' || rendered.error_type === 'timeout') {
            hasInfrastructureFailure = true;
          }
        }
        if (args.keepTemp) {
          output.input_file = rendered.input_file;
          output.output_file = rendered.output_file;
        }
      }

      if (output.ok) result.passed += 1;
      else result.failed += 1;
      result.blocks.push(output);
    });
  } finally {
    if (workdir && !args.keepTemp) {
      try { rmSync(workdir, { recursive: true, force: true }); } catch { /* best-effort cleanup */ }
    }
  }

  console.log(JSON.stringify(result, null, 2));
  if (hasInfrastructureFailure || result.blocks.some((block) => block.error_type === 'input_error' || block.error_type === 'environment_error' || block.error_type === 'dependency_error' || block.error_type === 'timeout')) {
    return 2;
  }
  return result.failed === 0 ? 0 : 1;
}

function main(): number {
  try {
    return run();
  } catch (err) {
    return emitTopLevelError('environment_error', err instanceof Error ? err.message : String(err));
  }
}

process.exitCode = main();
