const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("esagent/static/companion.js", "utf8");
const start = source.indexOf("  // The most recently opened/focused");
const end = source.indexOf("  function setUrl", start);
const reports = [], claims = [];
let playerState = 1, data = {title: "合成曲", author: "合成作者", video_id: "abcdefghijk"};
const ctx = vm.createContext({
  bgm: {mode: "embed", url: "chosen", ready: true, player: {
    getVideoData: () => data, getPlayerState: () => playerState}},
  parseYouTube: () => ({id: "abcdefghijk"}),
  post: (path, body) => path.endsWith("/open") ? new Promise(resolve => claims.push(resolve))
    : (reports.push(body), Promise.resolve({accepted: true}))
});
vm.runInContext(source.slice(start, end), ctx);
(async () => {
  const old = vm.runInContext("claimPlayback()", ctx);
  const latest = vm.runInContext("claimPlayback()", ctx);
  claims[1]({token: "new"}); await latest;
  claims[0]({token: "old"}); await old;
  assert.equal(reports.length, 1);
  assert.equal(reports[0].token, "new");
  assert.equal(reports[0].state, "playing");
  assert.equal(reports[0].title, "合成曲");
  for (const [code, state] of [[2,"paused"],[0,"ended"],[3,"buffering"],[-1,"selected"]]) {
    playerState = code; vm.runInContext("reportPlayback()", ctx);
    assert.equal(reports.at(-1).state, state);
  }
  vm.runInContext("playbackSelectionPending=true; reportPlayback()", ctx);
  assert.equal(reports.at(-1).state, "selected");
  assert.equal(reports.at(-1).title, "");
  vm.runInContext("playbackSelectionPending=false; playbackError=true; reportPlayback()", ctx);
  assert.equal(reports.at(-1).state, "error");
  ctx.bgm.mode = "window"; vm.runInContext("reportPlayback()", ctx);
  assert.equal(reports.at(-1).state, "external");
  assert.equal(reports.at(-1).title, "");
  ctx.bgm.mode = "embed"; ctx.bgm.url = ""; vm.runInContext("reportPlayback()", ctx);
  assert.equal(reports.at(-1).state, "none");
  assert.deepEqual(reports.map(x=>x.sequence), reports.map((_,i)=>i+1));
  console.log("playback UI reports passed");
})().catch(e => {console.error(e); process.exitCode=1;});
