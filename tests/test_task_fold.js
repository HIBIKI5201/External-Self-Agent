// The task list's categories fold and remember it (#173). Runs the real groupHtml from index.html.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('esagent/static/index.html', 'utf8');
const start = html.indexOf('// Categories of the task list can be folded');
const code = html.slice(start, html.indexOf('function render()', start));
const stored = {};
const ctx = vm.createContext({
  localStorage: {getItem: (k) => stored[k] ?? null, setItem: (k, v) => { stored[k] = v; }},
  esc: (s) => String(s).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])),
});
vm.runInContext(code + '\nthis.folded = folded; this.saveFolded = saveFolded; this.groupHtml = groupHtml;', ctx);
const row = (t) => `<row ${t.id}>`;
const tasks = [{id: 1, status: 'todo', due: '2026-10-15'}, {id: 2, status: 'doing', due: ''}, {id: 3, status: 'todo', due: '2026-10-20'}];

let open = ctx.groupHtml('Warframe', tasks, row, '2026-10-15');
assert(open.includes('<row 1>') && open.includes('<row 3>') && open.includes('aria-expanded="true"'));
assert(!open.includes('count') && !open.includes('soon'), 'an open category shows its rows, not a count');

ctx.folded.add('Warframe'); ctx.saveFolded();
let shut = ctx.groupHtml('Warframe', tasks, row, '2026-10-15');
assert(!shut.includes('<row') && shut.includes('（3）') && shut.includes('▸') && shut.includes('aria-expanded="false"'));
assert(shut.includes('class="soon"'), 'a task due today is marked on the folded name');
assert(!ctx.groupHtml('Warframe', [tasks[1], tasks[2]], row, '2026-10-15').includes('class="soon"'),
       'no mark without a task due today or overdue');
assert(!ctx.groupHtml('Warframe', [{id: 4, status: 'done', due: '2026-10-01'}], row, '2026-10-15').includes('class="soon"'),
       'a done task is not a warning');
assert.equal(stored['es.folded'], '["Warframe"]', 'the folded categories are saved');

// After a reload, the saved state comes back; a name with quotes is escaped in the button.
const again = vm.createContext({localStorage: {getItem: (k) => stored[k] ?? null, setItem() {}}, esc: ctx.esc});
vm.runInContext(code + '\nthis.folded = folded;', again);
assert(again.folded.has('Warframe'));
assert(ctx.groupHtml('A"B', tasks, row, '2026-10-15').includes('data-fold="A&quot;B"'));
console.log('task fold tests passed');
