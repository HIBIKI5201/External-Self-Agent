// 会話モード (#89): telling Es's own voice (heard through the mic) from the user's words.
const assert = require('node:assert/strict');
const { isEcho, words } = require('../esagent/static/listen.js');

const es = ['今は手をつけるべきタスクがありません。新しくやりたいことがあれば教えてください。'];

// Es's line coming back through the mic, written a little differently by the recogniser.
assert.equal(isEcho('今は手を付けるべきタスクがありません 新しくやりたいことがあれば教えてください', es), true);
assert.equal(isEcho('今は手を', es), true);                       // a partial result while she speaks
assert.equal(isEcho('タスクがありません', es), true);
// The user's own words, even when they share a few with Es's line.
assert.equal(isEcho('明日の予定を教えて', es), false);
assert.equal(isEcho('洗濯をタスクに追加して', es), false);
// Short answers to a question are the user's, unless they are the whole line.
assert.equal(isEcho('いく', ['休憩おわり。次、いく？']), false);
assert.equal(isEcho('うん', ['うん。']), true);
assert.equal(isEcho('はい', []), false);
assert.equal(isEcho('', es), true);                                 // nothing heard: nothing to send

// Final and still-changing words of a result list, from the first result not yet used.
const r = (t, f) => Object.assign([{ transcript: t }], { isFinal: f });
assert.deepEqual(words([r('洗濯を', true), r('追加', false)], 0), { final: '洗濯を', interim: '追加' });
assert.deepEqual(words([r('洗濯を', true), r('追加して', true)], 1), { final: '追加して', interim: '' });

// Run the actual page code with browser events and a deterministic clock.
const vm = require('node:vm');
const fs = require('node:fs');
function page(startErrors = 0) {
  let time = 0, id = 0;
  const jobs = new Map(), sessions = [], sent = [], posts = [];
  const button = { classList: { toggle() {} }, setAttribute() {} };
  const line = {};
  class Recognition {
    constructor() { sessions.push(this); }
    start() { if (startErrors-- > 0) throw new Error('start failed'); }
    abort() { this.aborted = true; }
    result(text) { this.onresult?.({ results: [r(text, true)] }); }
  }
  const window = { SpeechRecognition: Recognition, EsHooks: { send: text => sent.push(text) } };
  const context = { window, navigator: {}, document: { querySelector: s => s === '#listen' ? button : line },
    fetch: async (path, options) => { if (options) posts.push(path); return { ok: true, json: async () => ({ recent: [] }) }; },
    setTimeout: (fn, delay) => { jobs.set(++id, { fn, at: time + delay }); return id; },
    clearTimeout: key => jobs.delete(key) };
  vm.runInNewContext(fs.readFileSync(require.resolve('../esagent/static/listen.js'), 'utf8'), context);
  window.EsListen.init();
  const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
  const tick = async ms => {
    const end = time + ms;
    while (true) {
      const next = [...jobs].filter(([, job]) => job.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      jobs.delete(next[0]); time = next[1].at; next[1].fn(); await flush();
    }
    time = end; await flush();
  };
  return { sessions, sent, posts, line, button, context, flush, tick, toggle: () => button.onclick() };
}
async function lifecycle() {
  const p = page(); await p.toggle();
  p.sessions[0].result('最初の発言'); await p.tick(1200);
  assert.deepEqual(p.sent, ['最初の発言']);
  p.sessions[0].onend(); await p.tick(250);
  p.sessions.at(-1).result('再開後の発言'); await p.tick(1200);
  assert.deepEqual(p.sent, ['最初の発言', '再開後の発言'], 'first result of a new session must be consumed');

  const old = p.sessions.at(-1), stale = old.onresult;
  old.onend(); await p.toggle(); await p.toggle();
  const count = p.sessions.length;
  stale({ results: [r('古い発言', true)] });
  await p.tick(1500);
  assert.equal(p.sessions.length, count, 'old restart must not create another listener');
  assert.equal(p.sent.includes('古い発言'), false);

  const failing = page(3); await failing.toggle(); await failing.tick(10000);
  assert.equal(failing.button.textContent, '会話モード');
  assert.match(failing.line.textContent, /開始できません/);
  const recover = page(1); await recover.toggle(); await recover.tick(1000);
  recover.sessions.at(-1).result('回復した発言'); await recover.tick(1200);
  assert.deepEqual(recover.sent, ['回復した発言']);

  // Permission and echo checks can complete after the user turns the mode off.
  const q = page(); let permission;
  q.context.navigator.mediaDevices = { getUserMedia: () => new Promise(resolve => { permission = resolve; }) };
  const pending = q.toggle(); await q.toggle();
  permission({ getTracks: () => [] }); await pending;
  assert.equal(q.sessions.length, 0);
  const race = page(), permits = [];
  race.context.navigator.mediaDevices = { getUserMedia: () => new Promise(resolve => permits.push(resolve)) };
  const first = race.toggle(); await race.toggle(); const second = race.toggle();
  permits[0]({ getTracks: () => [] }); await first;
  assert.equal(race.sessions.length, 0, 'old permission must not start the new mode');
  permits[1]({ getTracks: () => [] }); await second;
  assert.equal(race.sessions.length, 1);
  const asyncPage = page(); await asyncPage.toggle(); const voices = [];
  asyncPage.context.fetch = (path, options) => {
    if (options) { asyncPage.posts.push(path); return Promise.resolve({ ok: true, json: async () => ({}) }); }
    return new Promise(resolve => voices.push(() => resolve({ json: async () => ({ recent: [] }) })));
  };
  asyncPage.sessions[0].result('送らない発言'); await asyncPage.tick(1200);
  await asyncPage.toggle(); voices.forEach(resolve => resolve()); await asyncPage.flush();
  assert.deepEqual(asyncPage.sent, []);
  assert.deepEqual(asyncPage.posts, [], 'stopped mode must not interrupt Es after a delayed echo check');
  console.log('test_listen.js ok (echo + lifecycle)');
}
lifecycle().catch(error => { console.error(error); process.exitCode = 1; });
