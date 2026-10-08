// Exercise actual player entry points; no real YouTube/Google/media requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('esagent/static/companion.js', 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const nodes = new Map();
const calls = [];
let errorOptions, playerOptions;
const $ = key => {
  if (!nodes.has(key)) nodes.set(key, {innerHTML: '', textContent: '', value: '', checked: false,
    close() { calls.push('close-library'); }, remove() {}, focus() {},
    classList: {contains() { return false; }, toggle() {}}});
  return nodes.get(key);
};
const ctx = vm.createContext({$, URL, encodeURIComponent, console, location: {origin: 'http://127.0.0.1:8766'},
  store: {get(key, fallback) {return key === 'bgm.mode' ? 'music' : fallback;}, set() {}},
  buildYouTube() {}, setBgmOpen() {}, refreshWin() {}, syncBgm() {calls.push('sync');},
  setUrl(url) { ctx.bgm.url = url; calls.push('url:' + url); },
  playBgm() {calls.push('play-embed');}, pauseEmbed() {calls.push('pause-embed');},
  isPlaying() {return false;}, media: async action => {calls.push('media:' + action);},
  post: async (path, body) => {calls.push(path + ':' + body.url);},
  say(text, options) {errorOptions = options;}, setTimeout() {},
  esc: value => value, embedVisible() {return true;},
  document: {querySelector() {throw Error('obsolete radio access');}},
  window: {YT: {Player: true}}, YT: {Player: function(id, options) {playerOptions = options;}},
});
vm.runInContext(section('  const bgm =', '  function parseYouTube') + '\nthis.bgm=bgm;' +
  section('  function parseYouTube', '  const yt =') +
  section('  async function useEmbedded', '  async function media') +
  section('  async function openWindow', '  function setBgmOpen') +
  section('  function loadApi', '  const isPlaying'), ctx);
(async () => {
  assert.equal(ctx.bgm.mode, 'embed', 'old music/window preference cannot reintroduce modes');
  vm.runInContext('buildBgm()', ctx);
  assert(!$('#bgm').innerHTML.includes('bgm-mode'));
  assert($('#bgm').innerHTML.includes('アプリ内再生に戻る'));
  calls.length = 0;
  ctx.bgm.mode = 'window';
  await vm.runInContext('playYouTubeVideo("example1234")', ctx);
  assert.deepEqual(calls.slice(0, 2), ['close-library', 'media:pause']);
  assert.equal(ctx.bgm.mode, 'embed');
  assert.equal(calls.at(-1), 'play-embed');
  ctx.bgm.mode = 'window'; calls.length = 0;
  await $('#bgm-select').onchange({target: {value: 'https://youtu.be/preset1234'}});
  assert.equal(ctx.bgm.mode, 'embed'); assert.equal(calls[0], 'media:pause');
  ctx.bgm.mode = 'window'; calls.length = 0;
  await $('#bgm-url').onkeydown({key: 'Enter', isComposing: false, preventDefault() {}, target: {value: 'https://youtu.be/pasted1234'}});
  assert.equal(ctx.bgm.mode, 'embed'); assert.equal(calls[0], 'media:pause');
  calls.length = 0;
  vm.runInContext('loadApi(() => {})', ctx);
  playerOptions.events.onError({data: 150});
  assert.equal(ctx.bgm.mode, 'embed'); assert(!calls.some(x => x.includes('/api/bgm/window')));
  assert.equal(errorOptions.buttons[0].label, 'YouTubeで開く');
  await errorOptions.buttons[0].onClick();
  assert.equal(ctx.bgm.mode, 'window'); assert(calls.includes('sync'));
  assert(calls.some(x => x.startsWith('/api/bgm/window:https://youtu.be/pasted1234')));
  calls.length = 0;
  await $('#bgm-win-return').onclick();
  assert.equal(ctx.bgm.mode, 'embed'); assert.equal(calls[0], 'media:pause');
  assert.equal(calls.at(-1), 'play-embed');
  console.log('single player, legacy mode, selections, explicit fallback and return: passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
