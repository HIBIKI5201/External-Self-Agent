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

console.log('test_listen.js ok');
