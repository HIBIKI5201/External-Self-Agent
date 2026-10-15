// The chat stays where the reader scrolled it (#247): run the actual renderLive() from index.html.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('esagent/static/index.html', 'utf8');
const code = source.slice(source.indexOf('const nearBottom ='), source.indexOf('// One pump loop') >= 0
  ? source.indexOf('// One pump loop') : source.indexOf('async function pump()'));
const log = {scrollHeight: 2000, clientHeight: 500, scrollTop: 0};
// Writing the live lines makes the log taller, as in the page.
const live = {html: '', set innerHTML(v) { this.html = v; log.scrollHeight += 100; }, get innerHTML() { return this.html; }};
const ctx = vm.createContext({
  $: (s) => (s === '#log' ? log : s === '#live' ? live : null),
  esc: (s) => String(s), performance: {now: () => 0},
  queue: [{text: 'こんにちは'}], busy: true, offline: false, thinkStart: 0,
  thinkStage: '', thinkDetail: '', thinkPartial: '',
});
vm.runInContext(code, ctx);

// Reading older messages: neither the thinking timer nor the refresh moves the view.
log.scrollTop = 300;
vm.runInContext('renderLive()', ctx);
assert.equal(log.scrollTop, 300, 'the thinking timer pulled the reader down');
vm.runInContext('renderLive(false)', ctx);
assert.equal(log.scrollTop, 300, 'the refresh pulled the reader down');
assert.match(live.innerHTML, /こんにちは/);

// At the bottom: new lines stay in view.
log.scrollTop = log.scrollHeight - log.clientHeight;
vm.runInContext('renderLive()', ctx);
assert.equal(log.scrollTop, log.scrollHeight);

// Sending always shows the new message.
log.scrollTop = 0;
vm.runInContext('renderLive(true)', ctx);
assert.equal(log.scrollTop, log.scrollHeight);

// render() passes whether the chat was at the bottom before it rebuilt the log.
assert.match(source, /const atBottom = nearBottom\(log\);[\s\S]*?renderLive\(atBottom\);/);
for (const call of ['saveQueue(); renderLive(true); pump();', 'queue.push(item); saveQueue(); renderLive(true);'])
  assert.ok(source.includes(call), call);
console.log('chat scroll: passed');
