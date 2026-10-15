// A failed add, check or 「終わった」 says why instead of failing silently (#249): runs the actual handlers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const said = [];
let refreshed = 0, failWith = null;
const call = async () => { if (failWith) throw new Error(failWith); return {}; };

// index.html: the add form and the task check box.
const page = fs.readFileSync('esagent/static/index.html', 'utf8');
const handlers = {};
const form = {addEventListener: (kind, fn) => { handlers.add = fn; }};
const doc = {addEventListener: (kind, fn) => { handlers[kind] = fn; }};
const ctx = vm.createContext({
  $: () => form, document: doc, api: call, refresh: () => { refreshed++; },
  FormData: function (target) { this.get = (k) => target[k].value; },
  window: {Es: {say: (text) => said.push(text)}},
});
vm.runInContext(page.slice(page.indexOf('$("#add").addEventListener("submit"'), page.indexOf('window.EsHooks =')), ctx);

// companion.js: the 「終わった」 button of the focus_end notice.
const comp = fs.readFileSync('esagent/static/companion.js', 'utf8');
let buttons = null;
const cctx = vm.createContext({
  bgm: {mode: 'embed', focusOnly: false}, chime() {}, flashMini() {}, post: call,
  say: (text, opts = {}) => { said.push(text); if (opts.buttons) buttons = opts.buttons; },
  window: {EsHooks: {refresh: () => { refreshed++; }}},
});
vm.runInContext(comp.slice(comp.indexOf('  function onEvent(ev)'), comp.indexOf('  // ---- sound')), cctx);

(async () => {
  const entry = () => ({title: {value: 'ES 提出'}, project: {value: ''}, due: {value: '2026-10-20'}});
  // Add: a failure keeps the typed title and says why; success clears it.
  failWith = 'タイトルがありません';
  let target = entry();
  await handlers.add({preventDefault() {}, target});
  assert.equal(target.title.value, 'ES 提出');
  assert.match(said.at(-1), /追加できなかった… タイトルがありません/);
  failWith = null; target = entry();
  await handlers.add({preventDefault() {}, target});
  assert.equal(target.title.value, '');

  // Check box: a refused completion puts the box back and says why.
  failWith = 'このタスクは読み取り専用です';
  const box = {dataset: {toggle: '7'}, disabled: false, checked: true};
  await handlers.change({target: box});
  assert.equal(box.checked, false);
  assert.equal(said.at(-1), 'このタスクは読み取り専用です');

  // 「終わった」: no cheer when the task was not completed.
  vm.runInContext('onEvent({kind: "focus_end", task_id: 7, task: "ES 提出", key: "k"})', cctx);
  const done = buttons.find((b) => b.label === '終わった');
  await done.onClick();
  assert.equal(said.at(-1), 'このタスクは読み取り専用です');
  assert.ok(!said.includes('やったね！'));
  failWith = null;
  await done.onClick();
  assert.equal(said.at(-1), 'やったね！');
  console.log('ui failures: passed');
})().catch((e) => { console.error(e); process.exitCode = 1; });
