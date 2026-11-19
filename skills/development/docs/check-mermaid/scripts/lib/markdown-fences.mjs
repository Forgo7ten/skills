/**
 * 按照 CommonMark 围栏规则从 Markdown 中提取 Mermaid 代码块。
 * 支持反引号（`）或波浪号（~）围栏、三个及以上围栏字符，以及最多三个前导空格缩进。
 *
 * 行号采用 1-based 计数：start_line 指向内容第一行；
 * end_line 指向闭合围栏所在行（若未闭合则指向最后一行）。
 */
export function extractMermaidBlocks(markdown) {
  const lines = markdown.split(/\r\n|\n|\r/);
  const blocks = [];
  let active = null;

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];

    if (active) {
      const closePattern = new RegExp(`^ {0,3}${active.char}{${active.length},}[ \\t]*$`);
      if (closePattern.test(line)) {
        if (active.isMermaid) {
          blocks.push({
            start_line: active.startLine,
            end_line: index + 1,
            code: lines.slice(active.openIndex + 1, index).join('\n'),
          });
        }
        active = null;
      }
      continue;
    }

    const opening = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (!opening) continue;

    const fence = opening[1];
    const info = opening[2].trim();
    // CommonMark 规范不允许反引号围栏的 info string 中出现反引号
    if (fence[0] === '`' && info.includes('`')) continue;

    const language = info.split(/\s+/, 1)[0]?.toLowerCase() ?? '';
    active = {
      char: fence[0],
      length: fence.length,
      openIndex: index,
      startLine: index + 2,
      isMermaid: language === 'mermaid',
    };
  }

  if (active?.isMermaid) {
    blocks.push({
      start_line: active.startLine,
      end_line: lines.length,
      code: lines.slice(active.openIndex + 1).join('\n'),
      structure_error: 'Unterminated Mermaid fenced code block',
    });
  }

  return blocks;
}
