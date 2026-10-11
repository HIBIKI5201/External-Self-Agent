// Exercise the actual inline queue/API code without sending requests to the app.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync('esagent/static/index.html', 'utf8');
let saved = JSON.stringify(['legacy', {text: 'queued', at: '2026-01-01T00:00:00'}]);
function screen() {
  const ctx = vm.createContext({crypto, localStorage: {
    getItem: () => saved, setItem: (key, value) => { saved = value; },
  }});
  const queue = source.slice(source.indexOf('const requestId ='), source.indexOf('const localIso ='));
  const save = source.slice(source.indexOf('const saveQueue ='), source.indexOf('let offline ='));
  vm.runInContext(queue + save, ctx);
  return JSON.parse(saved);
}
const first = screen();
assert.equal(first[0].text, 'legacy');
assert.equal(first[0].at, null);
assert.match(first[0].id, /^[a-f0-9]{32}$/);
assert.notEqual(first[0].id, first[1].id);
assert.deepEqual(screen(), first, 'reload retains the same IDs and sent times');
saved = JSON.stringify([{text: '切り替える', at: '2026-10-08T00:00:00.123Z', id: first[0].id,
  choice: {offer_id: 'a'.repeat(32), value: 'accept'}}]);
assert.deepEqual(screen()[0].choice, {offer_id: 'a'.repeat(32), value: 'accept'}, 'reload retains which offer was selected');
const timestampCode = source.slice(source.indexOf('const localIso ='), source.indexOf('// Questions about the wait'));
const clock = vm.createContext({});
vm.runInContext(timestampCode, clock);
assert.equal(vm.runInContext("localIso(new Date('2026-10-08T00:00:00.123Z'))", clock), '2026-10-08T00:00:00.123Z');
const apiCode = source.slice(source.indexOf('async function api('), source.indexOf('async function refresh('));
const ctx = vm.createContext({fetch: async () => ({json: async () => {throw new SyntaxError('truncated');}})});
vm.runInContext(apiCode, ctx);
(async () => {
  await assert.rejects(vm.runInContext('api("/api/chat", {})', ctx), e => e.offline === true);
  ctx.fetch = async () => ({ok: false, statusText: 'Conflict', json: async () => ({error: 'check history'})});
  await assert.rejects(vm.runInContext('api("/api/chat", {})', ctx), e => !e.offline && e.message === 'check history');
  console.log('queue migration, reload, truncated response, terminal conflict: passed');
})().catch(e => { console.error(e); process.exitCode = 1; });

// Validate and exercise the actual async submission code without the app.
const liveSource = source.slice(source.indexOf('function activeTarget()'), source.indexOf('// ---- slash command autocomplete'));
const inline = source.slice(source.indexOf('<script>') + 8, source.lastIndexOf('</script>'));
new vm.Script(inline);
const submitted = [];
let resolveFirst;
const firstReceipt = new Promise(resolve => { resolveFirst = resolve; });
const live = vm.createContext({
  crypto, S: {messages: []}, queue: [], busy: true, bypassRe: null,
  localIso: () => '2026-10-11T00:00:00.000Z',
  requestId: () => crypto.randomUUID().replaceAll('-', ''),
  saveQueue() {}, renderLive() {}, pump() {},
  window: {Es: {say() {}}},
  $: () => ({value: 'auto', addEventListener() {}}),
  setTimeout: fn => { fn(); },
  api: async (path, body) => {
    if (path === '/api/chat/submit') {
      submitted.push(body);
      if (submitted.length === 1) return firstReceipt;
      return {jobId: body.targetId || body.requestId, revision: body.targetId ? body.targetRevision + 1 : 1, state: 'queued'};
    }
    return {state: 'superseded', result: null};
  }
});
vm.runInContext(liveSource, live);
(async () => {
  await vm.runInContext('sendText("明日の予定を考えて")', live);
  const correction = vm.runInContext('sendText("いや、午前は空けたい")', live);
  assert.equal(submitted.length, 1, 'wait for receipt, not for generation');
  resolveFirst({jobId: submitted[0].requestId, revision: 1, state: 'queued'});
  await correction;
  assert.equal(submitted.length, 2);
  assert.equal(submitted[1].relation, 'correction');
  assert.equal(submitted[1].targetId, submitted[0].requestId);
  assert.equal(submitted[1].targetRevision, 1);
  assert.equal(vm.runInContext('queue[0].invalidated', live), true);
  await vm.runInContext('sendText("独立したお願い", "independent")', live);
  assert.equal(submitted[2].relation, 'independent');
  assert.equal(submitted[2].targetId, undefined);
  assert.equal(vm.runInContext('relationOf("クイズ", "auto")', live), 'independent');
  assert.equal(vm.runInContext('relationOf("取り消して", "auto")', live), 'cancel');
  assert.equal((await vm.runInContext('waitJob(queue[0])', live)).state, 'superseded');
  console.log('immediate receipt, correction target, independent request, stale revision: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

// 会話モード (#116): a spoken piece heard while the previous one still waits joins it.
const voiceSubmitted = [];
const voice = vm.createContext({
  crypto, S: {messages: []}, queue: [], busy: true, bypassRe: null, Date,
  localIso: () => '2026-10-12T00:00:00.000Z',
  requestId: () => crypto.randomUUID().replaceAll('-', ''),
  saveQueue() {}, renderLive() {}, pump() {},
  window: {Es: {say() {}}},
  $: () => ({value: 'auto', addEventListener() {}}),
  api: async (path, body) => {
    if (path !== '/api/chat/submit') return {};
    voiceSubmitted.push(body);
    if (body.relation === 'continue') return body.targetId === 'done'.padEnd(32, '0')
      ? {jobId: body.requestId, revision: 1, state: 'queued'}
      : {jobId: body.targetId, revision: body.targetRevision + 1, state: 'queued'};
    return {jobId: body.requestId, revision: 1, state: 'queued'};
  }
});
vm.runInContext(liveSource, voice);
(async () => {
  await vm.runInContext('sendText("君が言ってくれた", null, {via: "voice"})', voice);
  await vm.runInContext('sendText("チャットのときとかの", null, {via: "voice"})', voice);
  assert.equal(vm.runInContext('queue.length', voice), 1, 'the second piece joins the first');
  assert.equal(vm.runInContext('queue[0].text', voice), '君が言ってくれた チャットのときとかの');
  assert.equal(vm.runInContext('queue[0].parts', voice), 2);
  assert.equal(vm.runInContext('queue[0].revision', voice), 2);
  assert.equal(voiceSubmitted[1].relation, 'continue');
  assert.equal(voiceSubmitted[1].targetId, voiceSubmitted[0].requestId);
  await vm.runInContext('sendText("タイプした文")', voice);
  assert.equal(vm.runInContext('queue.length', voice), 2, 'typed text is never joined');
  // Long after the last piece, or after the answer: a message of its own.
  vm.runInContext('queue[0].lastPart = Date.now() - 60000', voice);
  vm.runInContext('queue.splice(1, 1)', voice);
  await vm.runInContext('sendText("しばらくして話した", null, {via: "voice"})', voice);
  assert.equal(vm.runInContext('queue.length', voice), 2);
  assert.equal(voiceSubmitted.at(-1).relation, 'independent');
  console.log('voice pieces join the waiting spoken message: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
