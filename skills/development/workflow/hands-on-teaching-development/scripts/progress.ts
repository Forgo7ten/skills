#!/usr/bin/env tsx
/** Durable, compact teaching cursors and batch queue helper. */
import { fileURLToPath } from 'node:url';
import { existsSync, mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { arg, commitCodePaths, gitRoot, gitStatus, gitText, gitWriteTree, parseCli, runCli, safeRelativePath } from './common.ts';

type StepStatus = 'pending' | 'done';
type BatchStatus = 'active' | 'paused' | 'finished';
interface TeachingStep {
  id: string;
  commit: string;
  title: string;
  paths: string[];
  status: StepStatus;
  completion?: { mode: 'handwritten' | 'accepted'; review: string; recordedAt: string };
}
interface Batch {
  id: string;
  module: string;
  status: BatchStatus;
  preparedHead: string;
  nextIndex: number;
  steps: TeachingStep[];
  blocker?: string;
}
interface State {
  version: 2;
  updatedAt: string;
  agent: { latestPreparedCommit: string | null; preparedSteps: number };
  user: { completedSteps: number; lastCompletedStep: string | null };
  batch: Batch | null;
}
interface ReviewInfo {
  status?: string;
  agentCommit?: string;
  createdAt?: string;
  paths?: string[];
  decision?: { choice: string; reason: string; recommended?: string; inspiration?: string; recordedAt: string };
  error?: string;
}
const STATE_BEGIN = '<!-- teaching-state-index:start -->';
const STATE_END = '<!-- teaching-state-index:end -->';

function repoPath(args: ReturnType<typeof parseCli>): string {
  const dir = arg(args, 'agent-dir');
  if (!dir) throw new Error('需要 --agent-dir <Agent Project 路径>');
  const repo = resolve(dir);
  if (gitRoot(repo) !== repo) throw new Error(`Agent Project 必须是独立 Git 仓库：${repo}`);
  return repo;
}
function withIndex<T>(source: string, start: string, end: string): T | null {
  const begin = source.indexOf(start);
  if (begin < 0) return null;
  const finish = source.indexOf(end, begin + start.length);
  if (finish < 0) throw new Error('进度索引缺少结束标记；拒绝猜测状态');
  return JSON.parse(source.slice(begin + start.length, finish).trim()) as T;
}
function renderBlock(start: string, end: string, value: unknown): string {
  return `${start}\n${JSON.stringify(value, null, 2)}\n${end}`;
}
function writeBlock(path: string, start: string, end: string, value: unknown, title: string): void {
  const block = renderBlock(start, end, value);
  const previous = existsSync(path) ? readFileSync(path, 'utf8') : '';
  const begin = previous.indexOf(start);
  let next: string;
  if (begin < 0) {
    // Keep old Markdown intact on upgrade. It may contain prior human notes.
    next = `# ${title}\n\n> 以下 JSON 区块由 progress.ts 维护，勿手工更改。\n\n${block}\n` +
      (previous.trim() ? `\n## 迁移前的人工记录（保留）\n\n${previous}\n` : '\n');
  } else {
    const finish = previous.indexOf(end, begin + start.length);
    if (finish < 0) throw new Error(`文件缺少结束标记：${path}`);
    next = previous.slice(0, begin) + block + previous.slice(finish + end.length);
  }
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.tmp-${process.pid}`;
  writeFileSync(tmp, next, 'utf8');
  renameSync(tmp, path);
}
function freshState(): State {
  return {
    version: 2, updatedAt: new Date().toISOString(),
    agent: { latestPreparedCommit: null, preparedSteps: 0 },
    user: { completedSteps: 0, lastCompletedStep: null }, batch: null,
  };
}
function stateFile(repo: string): string { return join(repo, 'docs', 'teaching-state.md'); }
function loadState(repo: string): State {
  const path = stateFile(repo);
  if (!existsSync(path)) throw new Error(`缺少 ${path}，先运行 progress.ts init`);
  const state = withIndex<State>(readFileSync(path, 'utf8'), STATE_BEGIN, STATE_END);
  if (!state || state.version !== 2) throw new Error('旧版教学状态未建立结构化索引；先运行 progress.ts init，并核对实际双游标');
  return state;
}
function saveState(repo: string, state: State): void {
  state.updatedAt = new Date().toISOString();
  writeBlock(stateFile(repo), STATE_BEGIN, STATE_END, state, 'Teaching State');
}
function isAncestor(repo: string, from: string, to: string): boolean {
  return gitStatus(repo, ['merge-base', '--is-ancestor', from, to]) === 0;
}
function commitSha(repo: string, name: string): string { return gitText(repo, ['rev-parse', `${name}^{commit}`]).trim(); }
function autoDiscoveryBase(repo: string, state: State, toRef: string, includeUserFloor: boolean): string | null {
  let base = state.agent.latestPreparedCommit && isAncestor(repo, state.agent.latestPreparedCommit, toRef)
    ? state.agent.latestPreparedCommit : null;
  if (includeUserFloor) {
    // Advance past completed ad-hoc single steps or reconciled [user] checkpoints so they are not re-queued.
    const latestUser = gitText(repo, ['log', '-1', '--format=%H', '--grep=^\\[user\\]', toRef]).trim();
    if (latestUser && (!base || isAncestor(repo, base, latestUser))) base = latestUser;
  }
  return base;
}
function status(repo: string, json: boolean, workspace: string): void {
  const state = loadState(repo);
  const batch = state.batch;
  const next = batch?.steps[batch.nextIndex] ?? null;
  const head = commitSha(repo, 'HEAD');
  const reviewFile = join(workspace, '.teaching', 'review', 'latest.json');
  const backupFile = join(workspace, '.teaching', 'backup', 'current', 'manifest.json');
  let reviewSnapshot: ReviewInfo | null = null;
  if (existsSync(reviewFile)) {
    try {
      const reviewed = JSON.parse(readFileSync(reviewFile, 'utf8')) as ReviewInfo;
      reviewSnapshot = reviewed ? {
        status: reviewed.status, agentCommit: reviewed.agentCommit,
        createdAt: reviewed.createdAt, paths: reviewed.paths, decision: reviewed.decision,
      } : null;
    } catch (err) { reviewSnapshot = { error: String(err) }; }
  }
  const userPath = join(workspace, 'user-project');
  let userGit: { stagedFiles: number; unstagedFiles: number; untrackedFiles: number } | null = null;
  let userIndexTree: string | null = null;
  if (existsSync(userPath) && gitRoot(userPath)) {
    const count = (parts: string[]) => gitText(userPath, parts).split('\n').filter(Boolean).length;
    userGit = {
      stagedFiles: count(['diff', '--cached', '--name-only']),
      unstagedFiles: count(['diff', '--name-only']),
      untrackedFiles: count(['ls-files', '--others', '--exclude-standard']),
    };
    userIndexTree = gitWriteTree(userPath);
  }
  const backupAvailable = existsSync(backupFile);
  let backupMatchesCurrentIndex: boolean | null = null;
  if (backupAvailable) {
    try {
      const manifest = JSON.parse(readFileSync(backupFile, 'utf8')) as { indexTree?: string; repoRoot?: string };
      const effectiveIndexTree = userIndexTree ?? (manifest.repoRoot && existsSync(manifest.repoRoot) && gitRoot(manifest.repoRoot)
        ? gitWriteTree(manifest.repoRoot) : null);
      if (effectiveIndexTree) backupMatchesCurrentIndex = manifest.indexTree === effectiveIndexTree;
    } catch { backupMatchesCurrentIndex = null; }
  }
  const base = autoDiscoveryBase(repo, state, head, !batch || batch.status === 'finished');
  const pendingRange = base ? `${base}..HEAD` : 'HEAD';
  const unregisteredAgentCommits = gitText(repo, ['log', '-n', '30', '--format=%H%x00%s', pendingRange])
    .trim().split('\n').filter(Boolean).map(line => {
      const [sha, subject] = line.split('\x00');
      return { sha, subject };
    }).filter(c => c.subject?.startsWith('[agent]') && !c.subject.includes(' initial(') && !c.subject.includes(' docs(') &&
      !batch?.steps.some(s => s.commit === c.sha));
  const reviewMatchesCurrentStep = Boolean(next && reviewSnapshot?.agentCommit === next.commit);
  const notesPath = join(repo, 'docs', 'NOTES.md');
  const missionPath = join(repo, 'docs', 'MISSION.md');
  const resourcesPath = join(repo, 'docs', 'RESOURCES.md');
  const report = {
    agentHead: head,
    agentPreparedHead: state.agent.latestPreparedCommit,
    agentPreparedSteps: state.agent.preparedSteps,
    userCompletedSteps: state.user.completedSteps,
    userLastCompletedStep: state.user.lastCompletedStep,
    batch: batch ? {
      id: batch.id, module: batch.module, status: batch.status,
      prepared: batch.steps.length, completed: batch.nextIndex, blocker: batch.blocker ?? null,
    } : null,
    currentUserStep: next ? { id: next.id, title: next.title, agentCommit: next.commit, paths: next.paths } : null,
    queuedSteps: batch ? batch.steps.slice(batch.nextIndex).map(s => ({ id: s.id, commit: s.commit, title: s.title })) : [],
    unregisteredAgentCommits,
    projectDocuments: {
      notes: notesPath,
      mission: missionPath,
      resources: resourcesPath,
      notesExists: existsSync(notesPath),
      missionExists: existsSync(missionPath),
      resourcesExists: existsSync(resourcesPath),
    },
    reviewSnapshot,
    reviewMatchesCurrentStep,
    backupAvailable,
    backupMatchesCurrentIndex,
    userGit,
    // A stale saved index is never silently accepted; a completed batch can still have new [user] commits after preparedHead.
    preparedHeadReachable: state.agent.latestPreparedCommit ? isAncestor(repo, state.agent.latestPreparedCommit, head) : null,
    stateCommitted: gitStatus(repo, ['diff', '--quiet', 'HEAD', '--', 'docs/teaching-state.md']) === 0,
    updatedAt: state.updatedAt,
  };
  if (json) { console.log(JSON.stringify(report, null, 2)); return; }
  console.log(`Agent HEAD: ${head.slice(0, 12)} | 已准备 ${report.agentPreparedSteps} 步`);
  console.log(`User 已完成 ${report.userCompletedSteps} 步；最近完成 ${report.userLastCompletedStep ?? '无'}`);
  console.log(`当前批次：${batch ? `${batch.id} [${batch.status}] ${batch.nextIndex}/${batch.steps.length}` : '无'}`);
  if (next) console.log(`下一教学步骤：${next.id} ${next.title} (${next.commit.slice(0, 12)})`);
  if (batch?.blocker) console.log(`阻塞：${batch.blocker}`);
  if (reviewSnapshot) {
    if (reviewMatchesCurrentStep) {
      console.log(`当前步骤 Review：${reviewSnapshot.status ?? reviewSnapshot.error ?? '未知'} (${reviewSnapshot.agentCommit?.slice(0, 12) ?? '未指定提交'})`);
      if (reviewSnapshot.decision) {
        const rec = reviewSnapshot.decision.recommended ? `（推荐：${reviewSnapshot.decision.recommended}）` : '';
        console.log(`本步骤用户选择：${reviewSnapshot.decision.choice}${rec}；${reviewSnapshot.decision.reason}`);
      }
    } else {
      console.log(`最近 Review（历史步骤 ${reviewSnapshot.agentCommit?.slice(0, 12) ?? '未知'}）：${reviewSnapshot.status ?? reviewSnapshot.error ?? '未知'}`);
    }
  }
  if (userGit) console.log(`User Git：已暂存 ${userGit.stagedFiles} / 未暂存 ${userGit.unstagedFiles} / 新文件 ${userGit.untrackedFiles}`);
  if (backupAvailable && backupMatchesCurrentIndex !== false) {
    console.log(`警告：存在与当前 Index 匹配的工作区备份 ${backupFile}，确认是否待恢复`);
  }
  if (unregisteredAgentCommits.length) console.log(`未登记到教学队列的最近 Agent Commit：${unregisteredAgentCommits.map(x => x.sha.slice(0, 8)).join(', ')}`);
  const docs = [
    report.projectDocuments.notesExists ? 'NOTES.md' : null,
    report.projectDocuments.missionExists ? 'MISSION.md' : null,
    report.projectDocuments.resourcesExists ? 'RESOURCES.md' : null,
  ].filter(Boolean);
  console.log(`项目文档（按需直接阅读）：${docs.length ? docs.join(', ') : '无'}`);
  if (!report.stateCommitted) console.warn('注意：教学状态尚未随 Agent Commit 提交');
  if (report.preparedHeadReachable === false) console.warn('警告：Agent 历史与已准备批次不一致，停止自动教学');
}
function init(repo: string): void {
  const path = stateFile(repo);
  if (!existsSync(path) || !withIndex<State>(readFileSync(path, 'utf8'), STATE_BEGIN, STATE_END)) {
    saveState(repo, freshState());
    console.warn('已创建结构化状态索引。如果项目已有教学进度，请核对旧版记录并重新登记未完成步骤；不会自动猜测。');
  }
  console.log(JSON.stringify({ status: 'INITIALIZED', state: path }, null, 2));
}
function discoverSteps(repo: string, state: State, startSeq: number, fromArg?: string, toArg?: string): TeachingStep[] {
  const toRef = commitSha(repo, toArg ?? 'HEAD');
  const head = commitSha(repo, 'HEAD');
  if (!isAncestor(repo, toRef, head)) throw new Error(`--to 不在当前 Agent 历史中：${toRef}`);
  let range: string;
  if (fromArg) {
    const fromSha = commitSha(repo, fromArg);
    if (!isAncestor(repo, fromSha, toRef)) throw new Error(`--from 必须是 --to 的祖先提交：${fromSha}`);
    range = `${fromSha}..${toRef}`;
  } else {
    const base = autoDiscoveryBase(repo, state, toRef, true);
    range = base ? `${base}..${toRef}` : toRef;
  }
  const lines = gitText(repo, ['log', '--reverse', '--format=%H%x00%s', range]).trim().split('\n').filter(Boolean);
  let seq = startSeq;
  const steps: TeachingStep[] = [];
  for (const line of lines) {
    const [sha, subject] = line.split('\x00');
    if (!subject?.startsWith('[agent]') || subject.includes(' initial(') || subject.includes(' docs(')) continue;
    const paths = commitCodePaths(repo, sha).map((p) => safeRelativePath(p)).sort();
    if (!paths.length) continue;
    seq += 1;
    const title = subject.replace(/^\[agent\]\s+[^:]+:\s*/, '').trim() || subject;
    steps.push({ id: `S${String(seq).padStart(3, '0')}`, commit: sha, title, paths, status: 'pending' });
  }
  if (!steps.length) throw new Error('未发现可登记的 [agent] 代码提交；请先提交实现，或使用 --steps-file 手动指定');
  return steps;
}
function prepare(repo: string, args: ReturnType<typeof parseCli>): void {
  const state = loadState(repo);
  if (state.batch && state.batch.nextIndex < state.batch.steps.length && state.batch.status !== 'paused') {
    throw new Error('上一批次仍有待学习步骤；不能覆盖教学队列');
  }
  if (state.batch && state.batch.nextIndex < state.batch.steps.length && !args.values.has('replace-paused')) {
    throw new Error('上一批次已暂停但尚未处理；仅在明确确认重排时使用 --replace-paused');
  }
  const batchId = arg(args, 'batch-id');
  const module = arg(args, 'module');
  const stepsFile = arg(args, 'steps-file');
  const auto = args.values.has('auto') || args.values.has('from');
  if (!batchId || !module || (!stepsFile && !auto)) {
    throw new Error('prepare 需要 --batch-id、--module，以及 --auto（或 --from <基线SHA>）或 --steps-file');
  }
  // Keep monotonic step IDs across replans, while deducting abandoned steps from preparedSteps count.
  const prevBatchMaxSeq = state.batch
    ? Math.max(0, ...state.batch.steps.map((s) => Number(s.id.replace(/^S/, '')) || 0))
    : 0;
  const startSeq = Math.max(state.agent.preparedSteps, prevBatchMaxSeq);
  const abandoned = state.batch && state.batch.status === 'paused'
    ? Math.max(0, state.batch.steps.length - state.batch.nextIndex)
    : 0;

  let steps: TeachingStep[];
  if (stepsFile) {
    const input = JSON.parse(readFileSync(resolve(stepsFile), 'utf8')) as unknown;
    if (!Array.isArray(input) || input.length === 0) throw new Error('steps-file 必须是非空步骤数组');
    const ids = new Set<string>();
    let previous: string | null = null;
    const head = commitSha(repo, 'HEAD');
    steps = input.map((item: any) => {
      if (!item || typeof item !== 'object' || typeof item.id !== 'string' || typeof item.commit !== 'string' || typeof item.title !== 'string') {
        throw new Error('每步需要 id、commit、title（paths 可省略，默认取该提交的代码文件）');
      }
      if (ids.has(item.id)) throw new Error(`重复步骤 ID: ${item.id}`);
      ids.add(item.id);
      const sha = commitSha(repo, item.commit);
      if (!isAncestor(repo, sha, head)) throw new Error(`提交不在当前 Agent 历史中：${sha}`);
      const subject = gitText(repo, ['show', '-s', '--format=%s', sha]).trim();
      if (!subject.startsWith('[agent]')) throw new Error(`不是 [agent] 实现提交：${sha} ${subject}`);
      if (previous && !isAncestor(repo, previous, sha)) throw new Error('批次提交必须按 Git 历史先后顺序排列');
      previous = sha;
      const rawPaths = Array.isArray(item.paths) && item.paths.every((p: unknown) => typeof p === 'string')
        ? item.paths : commitCodePaths(repo, sha);
      const paths: string[] = [...new Set<string>(rawPaths.map((p: string) => safeRelativePath(p)))].sort();
      if (!paths.length) throw new Error(`步骤 ${item.id} 的 paths 为空`);
      return { id: item.id, commit: sha, title: item.title, paths, status: 'pending' as const };
    });
  } else {
    steps = discoverSteps(repo, state, startSeq, arg(args, 'from'), arg(args, 'to'));
  }
  const lastCommit = steps[steps.length - 1].commit;
  state.batch = { id: batchId, module, status: 'active', preparedHead: lastCommit, nextIndex: 0, steps };
  state.agent.latestPreparedCommit = lastCommit;
  state.agent.preparedSteps = Math.max(0, state.agent.preparedSteps - abandoned) + steps.length;
  saveState(repo, state);
  console.log(JSON.stringify({
    status: 'PREPARED', batchId, count: steps.length, abandonedPreviousSteps: abandoned,
    first: steps[0].id, last: steps[steps.length - 1].id,
    steps: steps.map((s) => ({ id: s.id, commit: s.commit.slice(0, 12), title: s.title, paths: s.paths })),
  }, null, 2));
}
function complete(repo: string, args: ReturnType<typeof parseCli>): void {
  const state = loadState(repo);
  const current = state.batch?.steps[state.batch.nextIndex];
  if (!current || (state.batch?.status !== 'active' && state.batch?.status !== 'paused')) {
    throw new Error('当前没有可完成的教学步骤');
  }
  const stepId = arg(args, 'step') ?? current.id;
  const mode = arg(args, 'mode');
  const review = arg(args, 'review');
  if (stepId !== current.id) throw new Error(`必须按队列顺序完成：预期 ${current.id}，收到 ${stepId}`);
  if (mode !== 'handwritten' && mode !== 'accepted') throw new Error('需要 --mode handwritten|accepted');
  if (!review) throw new Error('需要 --review 描述已通过的 Review/移植验证结果');
  current.status = 'done';
  current.completion = { mode, review, recordedAt: new Date().toISOString() };
  state.user.completedSteps++;
  state.user.lastCompletedStep = current.id;
  state.batch!.nextIndex++;
  if (state.batch!.nextIndex >= state.batch!.steps.length) {
    state.batch!.status = 'finished';
    delete state.batch!.blocker;
  }
  saveState(repo, state);
  console.log(JSON.stringify({
    status: 'RECORDED', completed: current.id, batchStatus: state.batch!.status,
    next: state.batch!.steps[state.batch!.nextIndex]?.id ?? null,
    note: state.batch!.status === 'paused'
      ? '已记录当前步骤完成；剩余批次仍保持暂停，重做后续提交后运行 prepare --replace-paused --auto'
      : '请将状态、开发日志和本步文件一起创建 [user] Commit',
  }, null, 2));
}
function pause(repo: string, args: ReturnType<typeof parseCli>): void {
  const reason = arg(args, 'reason');
  if (!reason) throw new Error('pause 需要 --reason');
  const state = loadState(repo);
  if (!state.batch || state.batch.status !== 'active') throw new Error('没有运行中的教学批次');
  state.batch.status = 'paused';
  state.batch.blocker = reason;
  saveState(repo, state);
  console.log(JSON.stringify({
    status: 'PAUSED',
    reason,
    note: '后续步骤：1) 在 Agent Project 合入当前冲突步骤的用户改动，运行 progress.ts complete 并用 checkpoint.ts --source user 提交；2) 基于新代码重新修改并提交后续 [agent] Commit；3) 运行 progress.ts prepare --replace-paused --auto 登记新队列。',
  }, null, 2));
}
function rewind(repo: string, args: ReturnType<typeof parseCli>): void {
  const targetStep = arg(args, 'to-step') ?? arg(args, 'step');
  if (!targetStep) throw new Error('rewind 需要 --to-step <步骤ID>');
  const state = loadState(repo);
  if (!state.batch || state.batch.steps.length === 0) throw new Error('当前没有可回退的教学批次');
  const targetIndex = state.batch.steps.findIndex((s) => s.id === targetStep);
  if (targetIndex < 0) {
    throw new Error(`当前批次不存在步骤 ${targetStep}（可选：${state.batch.steps.map((s) => s.id).join(', ')}）`);
  }
  if (targetIndex >= state.batch.nextIndex) {
    const cursor = state.batch.steps[state.batch.nextIndex]?.id ?? '批次末尾';
    throw new Error(`步骤 ${targetStep} 尚未完成，无需回退（当前游标：${cursor}）`);
  }
  const rolledBack: string[] = [];
  for (let i = targetIndex; i < state.batch.steps.length; i += 1) {
    const step = state.batch.steps[i];
    if (step.status === 'done') rolledBack.push(step.id);
    step.status = 'pending';
    delete step.completion;
  }
  state.batch.nextIndex = targetIndex;
  state.batch.status = 'active';
  delete state.batch.blocker;
  state.user.completedSteps = Math.max(0, state.user.completedSteps - rolledBack.length);
  if (targetIndex > 0) {
    state.user.lastCompletedStep = state.batch.steps[targetIndex - 1].id;
  } else if (state.user.completedSteps === 0 || (state.user.lastCompletedStep && rolledBack.includes(state.user.lastCompletedStep))) {
    state.user.lastCompletedStep = null;
  }
  saveState(repo, state);
  console.log(JSON.stringify({
    status: 'REWOUND',
    toStep: targetStep,
    rolledBackSteps: rolledBack,
    batchStatus: state.batch.status,
    userCompletedSteps: state.user.completedSteps,
    note: '已回退本地教学游标；请核对 User Project 暂存区状态，并随下一次 checkpoint.ts 提交 docs/teaching-state.md',
  }, null, 2));
}
function main(): void {
  const args = parseCli(process.argv.slice(2));
  const command = args.positionals[0];
  if (!command || args.values.has('help')) {
    console.log(`progress.ts <init|status|prepare|complete|pause|rewind> --agent-dir <dir> [options]\n
status [--json] [--workspace <dir>]：只读双游标、批次队列、未登记提交与 Review/备份状态。
prepare --batch-id <id> --module <name> --auto [--from <基线SHA>] [--to <目标Rev>] [--replace-paused]
prepare --batch-id <id> --module <name> --steps-file <steps.json> [--replace-paused]
  --auto 自动从 Git 收集未登记的 [agent] 代码提交并提取标题与文件路径；--steps-file 可自定义覆盖。
complete [--step <id>] --mode handwritten|accepted --review <结果>（须在 [user] checkpoint 前）
pause --reason <原因>：保留旧提交，暂停冲突的后续步骤。
rewind --to-step <id>：游标误推进时将当前批次游标回退到指定步骤（重置该步及之后为 pending）。`);
    return;
  }
  const repo = repoPath(args);
  if (command === 'init') init(repo);
  else if (command === 'status') status(repo, args.values.has('json'), resolve(arg(args, 'workspace') ?? dirname(repo)));
  else if (command === 'prepare') prepare(repo, args);
  else if (command === 'complete') complete(repo, args);
  else if (command === 'pause') pause(repo, args);
  else if (command === 'rewind') rewind(repo, args);
  else throw new Error(`未知 progress 命令：${command}`);
}
const direct = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (direct) runCli(main);
