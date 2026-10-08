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
