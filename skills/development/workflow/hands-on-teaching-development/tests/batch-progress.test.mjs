import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const skillDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function resolveNodeFlags() {
  if (process.env.TEACHING_NATIVE_TS === '1') return ['--experimental-strip-types'];
  try {
    return ['--import', pathToFileURL(createRequire(import.meta.url).resolve('tsx')).href];
  } catch {
    const [major, minor] = process.versions.node.split('.').map(Number);
    if (major > 22 || (major === 22 && minor >= 6)) return ['--experimental-strip-types'];
    throw new Error('未检测到 tsx，且当前 Node 版本不支持 --experimental-strip-types');
  }
}

const nodeFlags = resolveNodeFlags();
function invoke(bin, args, expected = 0) {
  const r = spawnSync(bin, args, { encoding: 'utf8', env: { ...process.env, NODE_NO_WARNINGS: '1' } });
  if (r.error) throw r.error;
  assert.equal(r.status, expected, `${bin} ${args.join(' ')} status=${r.status}\nstdout=${r.stdout}\nstderr=${r.stderr}`);
  return r.stdout;
}
function git(dir, ...args) { return invoke('git', ['-C', dir, ...args]); }
function run(name, ...args) { return invoke(process.execPath, [...nodeFlags, join(skillDir, 'scripts', name), ...args]); }
function fails(name, pattern, ...args) {
  const r = spawnSync(process.execPath, [...nodeFlags, join(skillDir, 'scripts', name), ...args],
    { encoding: 'utf8', env: { ...process.env, NODE_NO_WARNINGS: '1' } });
  assert.notEqual(r.status, 0, `${name} should refuse unsafe action`);
  assert.match(r.stderr, pattern);
}
function put(path, value) { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, value); }
function initRepo(dir) {
  mkdirSync(dir, { recursive: true });
  invoke('git', ['init', '-q', dir]);
  git(dir, 'config', 'user.name', 'Batch Test');
  git(dir, 'config', 'user.email', 'batch@example.invalid');
}
function commit(dir, subject) {
  git(dir, 'add', '-A'); git(dir, 'commit', '-q', '-m', subject);
  return git(dir, 'rev-parse', 'HEAD').trim();
}
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'teaching-batch-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const user = join(root, 'user'); const agent = join(root, 'agent'); const workspace = join(root, 'workspace');
  initRepo(user); initRepo(agent); mkdirSync(workspace);
  put(join(agent, 'src', 'main.txt'), 'base\n');
  run('progress.ts', 'init', '--agent-dir', agent);
  commit(agent, '[agent] initial(init): baseline');
  put(join(user, 'src', 'main.txt'), 'base\n'); git(user, 'add', '-A');
  return { root, user, agent, workspace };
}
function setupBatch(f, withThird = false) {
  put(join(f.agent, 'src', 'main.txt'), 'one\n');
  const a1 = commit(f.agent, '[agent] feat(core): first step');
  put(join(f.agent, 'src', 'main.txt'), 'two\n');
  const a2 = commit(f.agent, '[agent] feat(core): second step');
  const steps = [
    { id: 'S001', commit: a1, title: 'First implementation', paths: ['src/main.txt'] },
    { id: 'S002', commit: a2, title: 'Second implementation', paths: ['src/main.txt'] },
  ];
  let a3;
  if (withThird) {
    put(join(f.agent, 'src', 'extra.txt'), 'test\n');
    a3 = commit(f.agent, '[agent] test(core): add test fixture');
    steps.push({ id: 'S003', commit: a3, title: 'Test coverage', paths: ['src/extra.txt'] });
  }
  const stepsFile = join(f.root, 'steps.json'); put(stepsFile, JSON.stringify(steps));
  run('progress.ts', 'prepare', '--agent-dir', f.agent, '--batch-id', 'core-v1', '--module', 'core', '--steps-file', stepsFile);
  commit(f.agent, '[agent] docs(core): register batch');
  return { a1, a2, a3 };
}
function reviewOne(f, commitSha) {
  return JSON.parse(run('review-diff.ts', 'review', '--user-dir', f.user, '--agent-dir', f.agent,
    '--workspace', f.workspace, '--agent-commit', commitSha, '--paths-file', writePaths(f.root, ['src/main.txt'])));
}
function writePaths(root, paths) { const path = join(root, 'paths.json'); put(path, JSON.stringify(paths)); return path; }

test('single-step review works before a batch is registered', t => {
  const f = fixture(t);
  put(join(f.agent, 'src', 'main.txt'), 'one\n');
  const a1 = commit(f.agent, '[agent] feat(core): first step');
  put(join(f.user, 'src', 'main.txt'), 'one\n');
  const review = reviewOne(f, a1);
  assert.equal(review.status, 'EXACT_MATCH');
  assert.equal(JSON.parse(run('review-diff.ts', 'stage-reviewed', '--user-dir', f.user,
    '--workspace', f.workspace)).status, 'STAGED');
  assert.equal(JSON.parse(run('review-diff.ts', 'verify-staged', '--user-dir', f.user,
    '--workspace', f.workspace)).status, 'VERIFIED');
  assert.deepEqual(JSON.parse(run('sync-user.ts', 'absorb', '--user-dir', f.user,
    '--agent-dir', f.agent, '--workspace', f.workspace)).copiedPaths, []);
});

test('agent batch ahead: user completes A1/A2 sequentially without overwriting A2', t => {
  const f = fixture(t); const { a1, a2 } = setupBatch(f);
  const headBefore = git(f.agent, 'rev-parse', 'HEAD').trim();
  fails('review-diff.ts', /禁止跳步 Review/, 'review', '--user-dir', f.user, '--agent-dir', f.agent,
    '--workspace', f.workspace, '--agent-commit', a2, '--paths-file', writePaths(f.root, ['src/main.txt']));
  put(join(f.user, 'src', 'main.txt'), 'one\n');
  const automatic = JSON.parse(run('review-diff.ts', 'review', '--user-dir', f.user, '--agent-dir', f.agent,
    '--workspace', f.workspace, '--paths-file', writePaths(f.root, ['src/main.txt'])));
  assert.equal(automatic.agentCommit, a1, 'missing --agent-commit should use queued step, not latest HEAD');
  const r1 = reviewOne(f, a1);
  assert.equal(r1.status, 'EXACT_MATCH');
  git(f.user, 'add', '--', 'src/main.txt');
  assert.equal(JSON.parse(run('review-diff.ts', 'verify-staged', '--user-dir', f.user, '--workspace', f.workspace)).status, 'VERIFIED');
  const absorb1 = JSON.parse(run('sync-user.ts', 'absorb', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace));
  assert.deepEqual(absorb1.copiedPaths, []);
  assert.equal(readFileSync(join(f.agent, 'src', 'main.txt'), 'utf8'), 'two\n');
  run('progress.ts', 'complete', '--agent-dir', f.agent, '--step', 'S001', '--mode', 'handwritten', '--review', 'EXACT_MATCH');
  commit(f.agent, '[user] docs(core): accept first step');
  put(join(f.user, 'src', 'main.txt'), 'two\n');
  const r2 = reviewOne(f, a2);
  assert.equal(r2.status, 'EXACT_MATCH', 'A2 should use parent A1 despite Agent HEAD including later [user] doc');
  git(f.user, 'add', '--', 'src/main.txt');
  assert.equal(JSON.parse(run('review-diff.ts', 'verify-staged', '--user-dir', f.user, '--workspace', f.workspace)).status, 'VERIFIED');
  assert.deepEqual(JSON.parse(run('sync-user.ts', 'absorb', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace)).copiedPaths, []);
  run('progress.ts', 'complete', '--agent-dir', f.agent, '--step', 'S002', '--mode', 'handwritten', '--review', 'EXACT_MATCH');
  commit(f.agent, '[user] docs(core): accept second step');
  const state = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--json'));
  assert.equal(state.batch.status, 'finished');
  assert.equal(state.userCompletedSteps, 2);
  assert.equal(state.currentUserStep, null);
  assert.notEqual(git(f.agent, 'rev-parse', 'HEAD').trim(), headBefore);
});

test('a divergent early step cannot overwrite files edited by future prepared commits', t => {
  const f = fixture(t); const { a1 } = setupBatch(f);
  put(join(f.user, 'src', 'main.txt'), 'different but reviewed\n');
  const review = reviewOne(f, a1);
  assert.equal(review.status, 'DIFFERENT');
  run('review-diff.ts', 'decide', '--workspace', f.workspace, '--choice', 'user', '--recommend', 'user', '--reason', '已审计且用户要求保留自定义逻辑');
  git(f.user, 'add', '--', 'src/main.txt');
  const before = git(f.agent, 'rev-parse', 'HEAD').trim();
  fails('sync-user.ts', /后续提交修改了相同文件/, 'absorb', '--user-dir', f.user,
    '--agent-dir', f.agent, '--workspace', f.workspace);
  assert.equal(readFileSync(join(f.agent, 'src', 'main.txt'), 'utf8'), 'two\n');
  assert.equal(git(f.agent, 'rev-parse', 'HEAD').trim(), before);
  run('progress.ts', 'pause', '--agent-dir', f.agent, '--reason', 'S001 changes overlap future commit');
  const state = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--json'));
  assert.equal(state.batch.status, 'paused');
  assert.match(state.batch.blocker, /overlap/);
});

test('queue completion rejects skipped steps while reporting current batch status', t => {
  const f = fixture(t); setupBatch(f, true);
  fails('progress.ts', /按队列顺序完成/, 'complete', '--agent-dir', f.agent, '--step', 'S002', '--mode', 'accepted', '--review', 'APPLIED');
  const status = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--json'));
  assert.equal(status.currentUserStep.id, 'S001');
  assert.equal(status.batch.prepared, 3);
  assert.equal(status.stateCommitted, true);
});

test('apply-agent transfers only queued A1 patch automatically without paths-file or commit flag, and allows single-step after batch finishes', t => {
  const f = fixture(t); const { a1, a2 } = setupBatch(f);
  const first = JSON.parse(run('sync-user.ts', 'apply-agent', '--user-dir', f.user,
    '--agent-dir', f.agent, '--workspace', f.workspace));
  assert.equal(first.status, 'APPLIED');
  assert.equal(first.agentCommit, a1);
  assert.equal(readFileSync(join(f.user, 'src', 'main.txt'), 'utf8'), 'one\n');
  run('progress.ts', 'complete', '--agent-dir', f.agent, '--mode', 'accepted', '--review', 'APPLIED');
  commit(f.agent, '[user] docs(core): accepted first prepared step');
  const second = JSON.parse(run('sync-user.ts', 'apply-agent', '--user-dir', f.user,
    '--agent-dir', f.agent, '--workspace', f.workspace));
  assert.equal(second.status, 'APPLIED');
  assert.equal(second.agentCommit, a2);
  assert.equal(readFileSync(join(f.user, 'src', 'main.txt'), 'utf8'), 'two\n');
  run('progress.ts', 'complete', '--agent-dir', f.agent, '--mode', 'accepted', '--review', 'APPLIED');
  commit(f.agent, '[user] docs(core): accepted second prepared step');

  // After a batch finishes, an ad-hoc single-step review/apply should not be blocked.
  put(join(f.agent, 'src', 'main.txt'), 'three\n');
  const a3 = commit(f.agent, '[agent] feat(core): ad-hoc single step');
  put(join(f.user, 'src', 'main.txt'), 'three\n');
  const r3 = JSON.parse(run('review-diff.ts', 'review', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace));
  assert.equal(r3.status, 'EXACT_MATCH');
  assert.equal(r3.agentCommit, a3);
});

test('initializing legacy state preserves previous narrative without assuming completed steps', t => {
  const f = fixture(t);
  const statePath = join(f.agent, 'docs', 'teaching-state.md');
  put(statePath, '# Previous state\n\n- **当前步骤**：S087\n');
  run('progress.ts', 'init', '--agent-dir', f.agent);
  const text = readFileSync(statePath, 'utf8');
  assert.match(text, /S087/);
  const state = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--json'));
  assert.equal(state.userCompletedSteps, 0);
  assert.equal(state.batch, null);
});

test('prepare --auto discovers agent commits and preserves freeform NOTES.md and teaching-state prose', t => {
  const f = fixture(t);
  const notesPath = join(f.agent, 'docs', 'NOTES.md');
  const statePath = join(f.agent, 'docs', 'teaching-state.md');
  const freeform = '# 项目笔记\n\n- 缓存模块需要 TTL\n- 输入边界做必要校验\n';
  put(notesPath, freeform);
  put(statePath, readFileSync(statePath, 'utf8') + '\n## 短期教学提示\n\n- 当前重点：错误处理\n');
  commit(f.agent, '[agent] docs(init): add freeform notes');

  put(join(f.agent, 'src', 'main.txt'), 'auto-1\n');
  const a1 = commit(f.agent, '[agent] feat(core): 第一步自动发现');
  put(join(f.agent, 'src', 'extra.txt'), 'auto-2\n');
  const a2 = commit(f.agent, '[agent] feat(core): 第二步自动发现');

  const prepared = JSON.parse(run('progress.ts', 'prepare', '--agent-dir', f.agent,
    '--batch-id', 'auto-v1', '--module', 'core', '--auto'));
  assert.equal(prepared.status, 'PREPARED');
  assert.equal(prepared.count, 2);
  assert.equal(prepared.first, 'S001');
  assert.equal(prepared.last, 'S002');
  assert.equal(readFileSync(notesPath, 'utf8'), freeform, 'progress.ts must not touch NOTES.md');
  assert.match(readFileSync(statePath, 'utf8'), /当前重点：错误处理/, 'freeform prose in teaching-state.md must survive JSON updates');

  // Review automatically uses queued commit & paths without --agent-commit or --paths-file.
  put(join(f.user, 'src', 'main.txt'), 'auto-1\n');
  const r1 = JSON.parse(run('review-diff.ts', 'review', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace));
  assert.equal(r1.status, 'EXACT_MATCH');
  assert.equal(r1.agentCommit, a1);
  assert.deepEqual(r1.paths, ['src/main.txt']);

  const status = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--json'));
  assert.equal(status.currentUserStep.agentCommit, a1);
  assert.equal(status.queuedSteps[1].commit, a2);
  assert.equal(status.projectDocuments.notesExists, true);
  assert(status.projectDocuments.notes.endsWith('docs/NOTES.md'));
  assert(status.projectDocuments.mission.endsWith('docs/MISSION.md'));
});

test('prepare --auto skips completed ad-hoc single steps, and paused replanning keeps dual cursors balanced', t => {
  const f = fixture(t);
  const { a1 } = setupBatch(f);

  // Finish B001 (S001, S002) via apply-agent and check stale backup warning does not trigger once index advances.
  run('sync-user.ts', 'apply-agent', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace);
  run('progress.ts', 'complete', '--agent-dir', f.agent, '--mode', 'accepted', '--review', 'APPLIED');
  commit(f.agent, '[user] docs(core): accept S001');
  run('sync-user.ts', 'apply-agent', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace);
  run('progress.ts', 'complete', '--agent-dir', f.agent, '--mode', 'accepted', '--review', 'APPLIED');
  commit(f.agent, '[user] docs(core): accept S002');

  // Ad-hoc single step after finished batch, completed with [user] commit.
  put(join(f.agent, 'src', 'adhoc.txt'), 'adhoc\n');
  commit(f.agent, '[agent] feat(extra): adhoc step');
  put(join(f.user, 'src', 'adhoc.txt'), 'adhoc\n');
  run('review-diff.ts', 'review', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace);
  run('review-diff.ts', 'stage-reviewed', '--user-dir', f.user, '--workspace', f.workspace);
  put(join(f.agent, 'docs', 'development-log.md'), 'adhoc done\n');
  commit(f.agent, '[user] docs(extra): completed adhoc step');

  // Next batch commits A5, A6 (both touch src/a.txt). prepare --auto must NOT re-queue adhoc.
  put(join(f.agent, 'src', 'a.txt'), 'v1\n');
  const a5 = commit(f.agent, '[agent] feat(mod): step a5');
  put(join(f.agent, 'src', 'a.txt'), 'v2\n');
  const a6 = commit(f.agent, '[agent] feat(mod): step a6');
  const beforePrepare = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--workspace', f.workspace, '--json'));
  assert.deepEqual(beforePrepare.unregisteredAgentCommits.map(c => c.sha).sort(), [a5, a6].sort());

  const b2 = JSON.parse(run('progress.ts', 'prepare', '--agent-dir', f.agent,
    '--batch-id', 'mod-v1', '--module', 'mod', '--auto'));
  assert.equal(b2.count, 2);
  assert.equal(b2.steps[0].commit, a5.slice(0, 12));
  commit(f.agent, '[agent] docs(mod): register mod-v1');

  // Divergent user on a5 conflicts with a6 -> pause, reconcile a5, complete while paused, replan a6'.
  put(join(f.user, 'src', 'a.txt'), 'user-v1\n');
  run('review-diff.ts', 'review', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace);
  run('review-diff.ts', 'decide', '--workspace', f.workspace, '--choice', 'user', '--reason', '保留用户实现');
  run('review-diff.ts', 'stage-reviewed', '--user-dir', f.user, '--workspace', f.workspace);
  fails('sync-user.ts', /后续提交修改了相同文件/, 'absorb', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace);

  run('progress.ts', 'pause', '--agent-dir', f.agent, '--reason', 'S003 与 S004 冲突');
  put(join(f.agent, 'src', 'a.txt'), 'user-v1\n');
  const completedPaused = JSON.parse(run('progress.ts', 'complete', '--agent-dir', f.agent, '--mode', 'handwritten', '--review', 'DIFFERENT(user, reconciled)'));
  assert.equal(completedPaused.batchStatus, 'paused');
  commit(f.agent, '[user] feat(mod): reconcile user a5');

  // Status on next step must NOT mislabel a5's review as current step's decision.
  const midStatusText = run('progress.ts', 'status', '--agent-dir', f.agent, '--workspace', f.workspace);
  assert(!midStatusText.includes('本步骤用户选择'), 'stale decision from completed step must not be labeled as current step choice');
  const midStatus = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--workspace', f.workspace, '--json'));
  assert.equal(midStatus.reviewMatchesCurrentStep, false);

  // Re-prepare a6' on top of user-v1 -> abandoned S004 is deducted, IDs stay monotonic (S005).
  put(join(f.agent, 'src', 'a.txt'), 'user-v1+v2\n');
  const a6New = commit(f.agent, '[agent] feat(mod): replanned a6');
  const replanned = JSON.parse(run('progress.ts', 'prepare', '--agent-dir', f.agent,
    '--batch-id', 'mod-v2', '--module', 'mod', '--replace-paused', '--auto'));
  assert.equal(replanned.count, 1);
  assert.equal(replanned.abandonedPreviousSteps, 1);
  assert.equal(replanned.first, 'S005');
  assert.equal(replanned.steps[0].commit, a6New.slice(0, 12));
  commit(f.agent, '[agent] docs(mod): register replanned batch');

  // Apply replanned a6' with dirty user draft so backup is created, then verify status does not emit false backup warning after index moves.
  put(join(f.user, 'src', 'a.txt'), 'half-written\n');
  run('sync-user.ts', 'apply-agent', '--user-dir', f.user, '--agent-dir', f.agent, '--workspace', f.workspace);
  run('progress.ts', 'complete', '--agent-dir', f.agent, '--mode', 'accepted', '--review', 'APPLIED');
  commit(f.agent, '[user] docs(mod): accept replanned a6');

  const finalStatus = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--workspace', f.workspace, '--json'));
  assert.equal(finalStatus.agentPreparedSteps, 4);
  assert.equal(finalStatus.userCompletedSteps, 4);
  assert.equal(finalStatus.batch.status, 'finished');
  assert.equal(finalStatus.backupAvailable, true);
  assert.equal(finalStatus.backupMatchesCurrentIndex, false);
  const finalStatusText = run('progress.ts', 'status', '--agent-dir', f.agent, '--workspace', f.workspace);
  assert(!finalStatusText.includes('确认是否待恢复'), 'historical backup after index advance should not trigger false warning');
  assert.ok(a1);
});

test('rewind recovers cursor and step counters when complete is accidentally over-advanced', t => {
  const f = fixture(t);
  const { a1 } = setupBatch(f);
  run('progress.ts', 'complete', '--agent-dir', f.agent, '--mode', 'accepted', '--review', 'APPLIED');
  run('progress.ts', 'complete', '--agent-dir', f.agent, '--mode', 'accepted', '--review', 'APPLIED');
  const finished = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--json'));
  assert.equal(finished.batch.status, 'finished');
  assert.equal(finished.userCompletedSteps, 2);

  // Rewind back to S002 first: S001 stays completed.
  const toSecond = JSON.parse(run('progress.ts', 'rewind', '--agent-dir', f.agent, '--to-step', 'S002'));
  assert.equal(toSecond.status, 'REWOUND');
  assert.deepEqual(toSecond.rolledBackSteps, ['S002']);
  assert.equal(toSecond.batchStatus, 'active');
  assert.equal(toSecond.userCompletedSteps, 1);

  // Rewinding to a not-yet-completed step is rejected.
  fails('progress.ts', /尚未完成，无需回退/, 'rewind', '--agent-dir', f.agent, '--to-step', 'S002');

  // Rewind all the way back to S001: cursor points to a1 again and counters reset cleanly.
  const toFirst = JSON.parse(run('progress.ts', 'rewind', '--agent-dir', f.agent, '--to-step', 'S001'));
  assert.deepEqual(toFirst.rolledBackSteps, ['S001']);
  assert.equal(toFirst.userCompletedSteps, 0);
  const restored = JSON.parse(run('progress.ts', 'status', '--agent-dir', f.agent, '--json'));
  assert.equal(restored.batch.status, 'active');
  assert.equal(restored.currentUserStep.id, 'S001');
  assert.equal(restored.currentUserStep.agentCommit, a1);
  assert.equal(restored.userLastCompletedStep, null);
});
