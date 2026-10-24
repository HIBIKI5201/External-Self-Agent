// The task column and the timer bar can be hidden for a narrow window, and stay hidden (#278).
// Runs the real layout code from index.html.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('esagent/static/index.html', 'utf8');
const start = html.indexOf('// Parts of the screen can be hidden');
const code = html.slice(start, html.indexOf('for (const part in LAYOUT_PARTS)', start));

function fakeDoc() {
  const classes = new Set();
  const buttons = {'#sideBtn': {}, '#barBtn': {}};
  for (const b of Object.values(buttons)) b.setAttribute = (k, v) => { b[k] = v; };
  return {
    classes, buttons,
    body: {classList: {toggle: (c, on) => { if (on) classes.add(c); else classes.delete(c); }}},
    querySelector: (s) => buttons[s] ?? null,
  };
}
function load(stored) {
  const ctx = vm.createContext({localStorage: {getItem: (k) => stored[k] ?? null, setItem: (k, v) => { stored[k] = v; }}});
  vm.runInContext(code + '\nthis.applyLayout = applyLayout; this.toggleLayout = toggleLayout;', ctx);
  return ctx;
}

const stored = {};
let ctx = load(stored), doc = fakeDoc();
ctx.applyLayout(doc);
assert.equal(doc.classes.size, 0, 'everything is shown at first');
assert.equal(doc.buttons['#sideBtn']['aria-pressed'], 'true');

ctx.toggleLayout('side', doc);
assert(doc.classes.has('hide-side') && !doc.classes.has('hide-bar'));
assert.equal(doc.buttons['#sideBtn']['aria-pressed'], 'false');
assert.equal(doc.buttons['#barBtn']['aria-pressed'], 'true');
assert.deepEqual(JSON.parse(stored['es.layout']), {side: false});

ctx.toggleLayout('bar', doc);
assert(doc.classes.has('hide-side') && doc.classes.has('hide-bar'));

// After a reload, the hidden parts stay hidden.
ctx = load(stored); doc = fakeDoc(); ctx.applyLayout(doc);
assert(doc.classes.has('hide-side') && doc.classes.has('hide-bar'));
ctx.toggleLayout('side', doc);
assert(!doc.classes.has('hide-side') && doc.classes.has('hide-bar'), 'pressing again shows it again');

// A broken or odd saved value shows everything instead of failing.
for (const bad of ['{', 'null', '5']) {
  const c = load({'es.layout': bad}), d = fakeDoc();
  c.applyLayout(d);
  assert.equal(d.classes.size, 0, `saved value ${bad}`);
}

// The CSS hides what the classes say, also under the narrow-window rules.
assert(/body\.hide-side aside \{ display: none; \}/.test(html));
assert(/body\.hide-side main \{ grid-template-columns: minmax\(0, 1fr\); \}/.test(html));
assert(/body\.hide-bar #scene #controls \{ display: none; \}/.test(html));
assert(/@media \(max-width: 1100px\)/.test(html));
console.log('layout toggle tests passed');
