// AS-11: which events fall on a day in the calendar pane (#255). Google ends all-day events on the
// next day (exclusive); timed events count on every day from their start to their end.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('esagent/static/calendar.js', 'utf8');
const start = source.indexOf('  function onDay');
const end = source.slice(start).search(/\r?\n  }\r?\n/);  // the checkout may have CRLF line ends
const body = source.slice(start, start + end) + '\n  }';
const ctx = vm.createContext({data: {events: [
  {title: '終日1日', all_day: true, start: '2026-10-14', end: '2026-10-15'},
  {title: '終日3日', all_day: true, start: '2026-10-20', end: '2026-10-23'},
  {title: '会議', all_day: false, start: '2026-10-14T10:00:00+09:00', end: '2026-10-14T11:00:00+09:00'},
  {title: '夜通し', all_day: false, start: '2026-10-15T23:00:00+09:00', end: '2026-10-16T01:00:00+09:00'},
]}});
vm.runInContext(body + '\nthis.onDay = onDay;', ctx);
const on = (d) => ctx.onDay(d).map((e) => e.title);
assert.deepEqual(on('2026-10-14'), ['終日1日', '会議']);
assert.deepEqual(on('2026-10-15'), ['夜通し'], 'an all-day end day is exclusive');
assert.deepEqual(on('2026-10-16'), ['夜通し'], 'a timed event counts on the day it ends');
assert.deepEqual(on('2026-10-20'), ['終日3日']);
assert.deepEqual(on('2026-10-22'), ['終日3日']);
assert.deepEqual(on('2026-10-23'), []);
console.log('calendar day tests passed');
