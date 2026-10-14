// The calendar pane keeps what the user is typing across reloads (#250). No real Google requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('esagent/static/calendar.js', 'utf8');

// A tiny DOM: #cal holds the form fields and the <details>; setting innerHTML "rebuilds" the pane,
// which empties every field and closes the list, as a real redraw does.
function field(tagName, name) { return {tagName, name, value: '', options: name === 'calendar' ? [{value: 'cal-1'}] : undefined}; }
let fields, details, draws = 0, timers = [];
function rebuild() {
  fields = {title: field('INPUT', 'title'), date: field('INPUT', 'date'), start: field('INPUT', 'start'),
            end: field('INPUT', 'end'), calendar: field('SELECT', 'calendar')};
  details = {open: false};
}
rebuild();
const form = {querySelector: (s) => fields[(s.match(/name="(\w+)"/) || [])[1]] || null};
const listeners = {};
const root = {
  classList: {toggle() {}},
  set innerHTML(v) { draws += 1; rebuild(); },
  querySelector: (s) => s === '#cal-add' ? form : s === 'details.cal-choose' ? details : null,
  contains: (el) => Object.values(fields).includes(el),
  addEventListener: (type, fn) => { listeners[type] = fn; },
};
const document = {
  activeElement: null,
  querySelector: (s) => s === '#cal' ? root : s === '#cal-toggle' ? {} : null,
  querySelectorAll: () => [],
};
const reply = {ready: true, calendars: [{id: 'cal-1', name: '私', writable: true, selected: true, color: '#123456'}],
               events: [], sync: {}};
const ctx = vm.createContext({
  document, console, localStorage: {getItem() { return '1'; }, setItem() {}},
  fetch: async () => ({ok: true, json: async () => reply}),
  setInterval: (fn) => { timers.push(fn); }, setTimeout: (fn) => fn(), window: {},
});
vm.runInContext(source, ctx);
const settle = () => new Promise((r) => setImmediate(r));

(async () => {
  ctx.window.EsCalendar.init();
  await settle();
  const auto = timers[0];
  assert(auto, 'a 5-minute reload is scheduled');

  // Typing an event: the automatic reload does not rebuild the pane.
  fields.title.value = '歯医者'; fields.start.value = '10:00';
  document.activeElement = fields.title;
  const before = draws;
  auto(); await settle();
  assert.equal(draws, before, 'no redraw while a field of the pane has the focus');
  assert.equal(fields.title.value, '歯医者');

  // Leaving the pane draws the skipped reload, and what was typed comes back.
  document.activeElement = null;
  listeners.focusout(); await settle();
  assert.equal(draws, before + 1, 'the skipped reload is drawn after the focus leaves');
  assert.equal(fields.title.value, '歯医者'); assert.equal(fields.start.value, '10:00');

  // An open calendar list stays open across a reload, and nothing typed means a plain redraw.
  fields.title.value = ''; fields.start.value = ''; details.open = true;
  auto(); await settle();
  assert.equal(draws, before + 2, 'not typing: the reload redraws as before');
  assert.equal(details.open, true, 'the list of calendars stays open');
  assert.equal(fields.title.value, '');
  console.log('calendar pane tests passed');
})().catch((e) => { console.error(e); process.exit(1); });
