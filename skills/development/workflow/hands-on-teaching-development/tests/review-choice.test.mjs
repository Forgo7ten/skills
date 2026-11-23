import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
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

const flags = resolveNodeFlags();
function cmd(bin,args,code=0) {
  const v=spawnSync(bin,args,{encoding:'utf8',env:{...process.env,NODE_NO_WARNINGS:'1'}});
  assert.equal(v.status,code,`${bin} ${args.join(' ')} exit=${v.status}\n${v.stdout}\n${v.stderr}`);
  return v.stdout;
}
function git(repo,...args){return cmd('git',['-C',repo,...args])}
function invoke(script, ...args){return cmd(process.execPath,[...flags,join(skillDir,'scripts',script),...args])}
function rejected(script, pattern,...args){
 const v=spawnSync(process.execPath,[...flags,join(skillDir,'scripts',script),...args],{encoding:'utf8',env:{...process.env,NODE_NO_WARNINGS:'1'}});
 assert.notEqual(v.status,0); assert.match(v.stderr, pattern);
}
function write(path, body){mkdirSync(dirname(path),{recursive:true});writeFileSync(path,body)}
function fixture(t,{future=false}={}){
 const root=mkdtempSync(join(tmpdir(),'review-choice-'));
 t.after(()=>rmSync(root,{recursive:true,force:true}));
 const user=join(root,'user'); const agent=join(root,'agent'); const workspace=join(root,'workspace');
 for(const repo of [user,agent]){mkdirSync(repo,{recursive:true});cmd('git',['init','-q',repo]);git(repo,'config','user.name','Reviewer');git(repo,'config','user.email','r@example.invalid')}
 mkdirSync(workspace);
 write(join(agent,'src','a.ts'),'export const a = 0;\n');
 git(agent,'add','-A');git(agent,'commit','-qm','[agent] initial(init): base');
 write(join(user,'src','a.ts'),'export const a = 0;\n');git(user,'add','-A');
 write(join(agent,'src','a.ts'),'export const a = 1;\n');
 git(agent,'add','-A');git(agent,'commit','-qm','[agent] feat(a): a');
 const first=git(agent,'rev-parse','HEAD').trim();
 if(future){write(join(agent,'src','b.ts'),'export const b = 2;\n');git(agent,'add','-A');git(agent,'commit','-qm','[agent] feat(b): b')}
 return {root,user,agent,workspace,first};
}
function review(f){return JSON.parse(invoke('review-diff.ts','review','--user-dir',f.user,'--agent-dir',f.agent,'--workspace',f.workspace,'--agent-commit',f.first));}
function stage(f){return JSON.parse(invoke('review-diff.ts','stage-reviewed','--user-dir',f.user,'--workspace',f.workspace));}
function verify(f){return JSON.parse(invoke('review-diff.ts','verify-staged','--user-dir',f.user,'--workspace',f.workspace));}

test('EXACT_MATCH can stage only reviewed paths and verify without a user commit',t=>{
 const f=fixture(t);
 write(join(f.user,'src','a.ts'),'export const a = 1;\n');
 const r=review(f);
 assert.equal(r.status,'EXACT_MATCH');assert.equal(r.diffs,null);
 assert.equal(stage(f).status,'STAGED');assert.equal(verify(f).status,'VERIFIED');
 assert.equal(git(f.user,'rev-list','--all','--count').trim(),'0');
 assert.equal(git(f.user,'show',':src/a.ts'),'export const a = 1;\n');
});

test('DIFFERENT returns inline comparison and refuses premature stage or absorb',t=>{
 const f=fixture(t);
 write(join(f.user,'src','a.ts'),'export const a = 100;\n');
 const r=review(f);
 assert.equal(r.status,'DIFFERENT');
 assert.match(r.diffs.userVsAgent.text,/100/);
 assert.match(r.diffs.userVsAgent.text,/1/);
 assert.match(r.diffs.userPatch.text,/100/);
 assert.match(r.diffs.agentPatch.text,/1/);
 assert.equal(r.diffs.userVsAgent.truncated,false);
 rejected('review-diff.ts',/先审计/,'stage-reviewed','--user-dir',f.user,'--workspace',f.workspace);
 const paths=join(f.root,'paths.json');write(paths,JSON.stringify(['src/a.ts']));
 rejected('sync-user.ts',/尚未明确选择 Agent 方案/,'apply-agent','--user-dir',f.user,
   '--agent-dir',f.agent,'--workspace',f.workspace,'--agent-commit',f.first,'--paths-file',paths);
 git(f.user,'add','--','src/a.ts');
 rejected('sync-user.ts',/尚未审计并确认/,'absorb','--user-dir',f.user,'--agent-dir',f.agent,'--workspace',f.workspace);
});

test('after decision USER the script stages, verifies and absorb accepts reviewed alternative',t=>{
 const f=fixture(t,{future:true});
 write(join(f.user,'src','a.ts'),'export const a = 7;\n');
 const r=review(f);assert.equal(r.status,'DIFFERENT');
 const chosen=JSON.parse(invoke('review-diff.ts','decide','--workspace',f.workspace,'--choice','user',
   '--recommend','agent','--inspiration','用户提出了替代的数据流设计','--reason','已听取推荐，但用户选择保留等价实现'));
 assert.equal(chosen.status,'DECIDED');
 assert.equal(chosen.decision.recommended,'agent');
 assert.match(chosen.decision.inspiration,/数据流/);
 assert.equal(stage(f).status,'STAGED');assert.equal(verify(f).status,'VERIFIED');
 const absorbed=JSON.parse(invoke('sync-user.ts','absorb','--user-dir',f.user,'--agent-dir',f.agent,'--workspace',f.workspace));
 assert.deepEqual(absorbed.copiedPaths,['src/a.ts']);
 assert.equal(readFileSync(join(f.agent,'src','a.ts'),'utf8'),'export const a = 7;\n');
 assert.equal(readFileSync(join(f.agent,'src','b.ts'),'utf8'),'export const b = 2;\n');
});

test('selecting AGENT rejects user staging; apply-agent backs up user draft and stages patch',t=>{
 const f=fixture(t);
 write(join(f.user,'src','a.ts'),'export const a = 999;\n');
 review(f);
 invoke('review-diff.ts','decide','--workspace',f.workspace,'--choice','agent','--recommend','agent',
   '--inspiration','用户方案提供更清晰的错误信息，可留待后续优化','--reason','用户选择教学方案');
 rejected('review-diff.ts',/先审计/,'stage-reviewed','--user-dir',f.user,'--workspace',f.workspace);
 const paths=join(f.root,'paths.json');write(paths,JSON.stringify(['src/a.ts']));
 const applied=JSON.parse(invoke('sync-user.ts','apply-agent','--user-dir',f.user,'--agent-dir',f.agent,'--agent-commit',f.first,'--workspace',f.workspace,'--paths-file',paths));
 assert.equal(applied.status,'APPLIED');
 assert.equal(readFileSync(join(f.user,'src','a.ts'),'utf8'),'export const a = 1;\n');
});

test('Review invalidation after source changes blocks stage and preserves index',t=>{
 const f=fixture(t);
 write(join(f.user,'src','a.ts'),'export const a = 1;\n');review(f);
 const oldIndex=git(f.user,'write-tree').trim();
 write(join(f.user,'src','a.ts'),'export const a = 2;\n');
 rejected('review-diff.ts',/Review 后文件/,'stage-reviewed','--user-dir',f.user,'--workspace',f.workspace);
 assert.equal(git(f.user,'write-tree').trim(),oldIndex);
});

test('whitespace-only differences are hints, not automatic approval, and decide works without extra evaluate step',t=>{
 const f=fixture(t);
 write(join(f.user,'src','a.ts'),'export const a = 1 ;\n');
 const r=review(f);
 assert.equal(r.status,'DIFFERENT');
 assert.deepEqual(r.whitespaceOnlyCandidates,['src/a.ts']);
 assert.equal(r.diffs.userVsAgent.truncated,false);
 const decided=JSON.parse(invoke('review-diff.ts','decide','--workspace',f.workspace,'--choice','user','--reason','仅空白风格差异，用户保留自写格式'));
 assert.equal(decided.status,'DECIDED');
 assert.equal(decided.decision.recommended,undefined);
 assert.equal(stage(f).status,'STAGED');
});

test('updating decision overwrites previous choice and rejects invalid recommend value',t=>{
 const f=fixture(t);write(join(f.user,'src','a.ts'),'export const a = 15;\n');
 review(f);
 rejected('review-diff.ts',/--recommend 必须为 agent 或 user/,'decide','--workspace',f.workspace,'--choice','user','--recommend','both','--reason','非法推荐值');
 invoke('review-diff.ts','decide','--workspace',f.workspace,'--choice','user','--reason','用户先选了自写');
 invoke('review-diff.ts','decide','--workspace',f.workspace,'--choice','agent','--recommend','agent','--reason','重新讨论后改选教学实现');
 const saved=JSON.parse(readFileSync(join(f.workspace,'.teaching','review','latest.json'),'utf8'));
 assert.equal(saved.decision.choice,'agent');
 assert.equal(saved.decision.recommended,'agent');
 rejected('review-diff.ts',/先审计/,'stage-reviewed','--user-dir',f.user,'--workspace',f.workspace);
});
