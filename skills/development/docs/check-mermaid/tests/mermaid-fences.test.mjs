import test from 'node:test';
import assert from 'node:assert/strict';
import { extractMermaidBlocks } from '../scripts/lib/markdown-fences.mjs';

test('extracts Mermaid blocks with backtick and tilde fences and correct 1-based lines', () => {
  const markdown = [
    'Intro',
    '````mermaid',
    'flowchart TD',
    '  A --> B',
    '````',
    '~~~mermaid title="second"',
    'sequenceDiagram',
    '  Alice->>Bob: hi',
    '~~~',
  ].join('\n');
  const blocks = extractMermaidBlocks(markdown);
  assert.equal(blocks.length, 2);
  assert.equal(blocks[0].start_line, 3);
  assert.equal(blocks[0].end_line, 5);
  assert.equal(blocks[1].start_line, 7);
  assert.equal(blocks[1].end_line, 9);
});

test('ignores Mermaid-looking text inside a non-Mermaid fenced block', () => {
  const markdown = [
    '```markdown',
    '```mermaid',
    'flowchart TD',
    'A --> B',
    '```',
    '```',
  ].join('\n');
  assert.deepEqual(extractMermaidBlocks(markdown), []);
});

test('reports an unterminated Mermaid fence as a structure error', () => {
  const blocks = extractMermaidBlocks('text\n~~~mermaid\nflowchart TD\nA --> B');
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].start_line, 3);
  assert.equal(blocks[0].end_line, 4);
  assert.match(blocks[0].structure_error, /Unterminated/);
});
