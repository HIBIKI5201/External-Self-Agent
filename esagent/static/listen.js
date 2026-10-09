// 会話モード (#89): while it is on, this window listens with Edge's built-in speech recognition
// (Web Speech API; the audio goes to Microsoft's speech service), and sends what you said as an
// ordinary chat message once you stop talking. Es's voice comes out of Windows, not this page, so
// the browser cannot cancel it: a recognised line that matches what Es is saying is dropped as an
// echo; anything else is you, and stops Es's reading (#44). Nothing is recorded or kept.
(() => {
  const SILENCE_MS = 1200;   // this long without new words ends what you are saying
  const ECHO_SECS = 8;       // Es's lines this recent can come back through the mic
  const MAX_FAILS = 3;       // give up after this many network errors in a row

  // ---- pure helpers (tests/test_listen.js) --------------------------------------------
  const norm = (s) => String(s || "").normalize("NFKC").toLowerCase()
    .replace(/[\s、。，．,.!！?？・「」『』（）()…ー〜~"'“”]/g, "");
  const grams = (s) => { const out = new Set(); for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2)); return out; };

  // Whether `heard` is Es's own voice picked up by the mic: most of its character pairs appear in
  // a line Es is speaking or has just spoken. Recognition may write words differently (kana or
  // kanji), so this is an overlap, not an exact match.
  function isEcho(heard, esLines) {
    const h = norm(heard);
    if (!h) return true;
    for (const line of esLines || []) {
      const l = norm(line);
      if (!l) continue;
      // A short answer ("いく" to "次、いく？") is yours unless it is the whole line.
      if (h.length < 4) { if (h === l) return true; continue; }
      if (l.includes(h)) return true;
      const hg = grams(h);
      if (!hg.size) continue;
      const lg = grams(l);
      let hit = 0;
      hg.forEach((g) => { if (lg.has(g)) hit++; });
      if (hit / hg.size >= 0.5) return true;
    }
    return false;
  }

  // The words of one recognition result list: what is final so far and what is still changing.
  function words(results, from) {
    let final = "", interim = "";
    for (let i = from; i < results.length; i++) {
      const t = results[i][0].transcript;
      if (results[i].isFinal) final += t; else interim += t;
    }
    return { final, interim };
  }

  if (typeof module !== "undefined") { module.exports = { isEcho, words, norm }; return; }

  // ---- the page -----------------------------------------------------------------------
  const $ = (s) => document.querySelector(s);
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const channel = "BroadcastChannel" in window ? new BroadcastChannel("es-listen") : null;
  let rec = null, on = false, heard = "", done = 0, timer = null, fails = 0, barged = false, checking = false;

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-EsAgent": "1" }, body: JSON.stringify(body || {}) });
    if (!r.ok) throw new Error(r.statusText);
    return r.json();
  }
  async function esLines() {
    try { return (await (await fetch("/api/voice")).json()).recent || []; } catch { return []; }
  }

  function show(state, text = "") {
    const btn = $("#listen");
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.textContent = on ? "会話モード：オン" : "会話モード";
    const line = $("#listen-line");
    line.hidden = !on && !state && !text;  // a reason the mode stopped stays visible
    line.textContent = text ? `${state}${text}` : state;
  }

  async function heardSome(text) {
    // The first non-echo words of what you are saying stop Es's reading (barge-in).
    if (barged || checking || !text.trim()) return;
    checking = true;  // results keep arriving while Es's lines are fetched
    try {
      if (isEcho(text, await esLines())) return;
    } finally {
      checking = false;
    }
    barged = true;
    post("/api/voice/stop").catch(() => {});
  }

  async function finish() {
    timer = null;
    const text = heard.trim();
    heard = ""; barged = false;
    if (!text || !on) return show("聞いています…");
    if (isEcho(text, await esLines())) return show("聞いています…（Es の声は送りません）");
    show("送信：", text);
    window.EsHooks?.send?.(text);
    setTimeout(() => on && show("聞いています…"), 1500);
  }

  function start() {
    if (!Rec) {
      on = false;
      return show("この画面では音声認識を使えません（Edge で開いてください）");
    }
    rec = new Rec();
    rec.lang = "ja-JP";
    rec.continuous = true;
    rec.interimResults = true;
    done = 0;
    rec.onresult = (e) => {
      fails = 0;
      const w = words(e.results, done);
      for (let i = done; i < e.results.length; i++) if (e.results[i].isFinal) done = i + 1;
      heard += w.final;
      const now = (heard + w.interim).trim();
      if (!now) return;
      show("認識中：", now);
      heardSome(now);
      clearTimeout(timer);
      timer = setTimeout(finish, SILENCE_MS);
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        stop(); return show("マイクが許可されていません。Edge の設定（Cookie とサイトのアクセス許可 → マイク）で 127.0.0.1 を許可してください");
      }
      if (e.error === "audio-capture") { stop(); return show("マイクが見つかりません"); }
      if (e.error === "network" && ++fails >= MAX_FAILS) { stop(); return show("音声認識のサービスにつながりません"); }
      if (!["no-speech", "aborted", "network"].includes(e.error)) show(`音声認識のエラー（${e.error}）`);
    };
    rec.onend = () => {  // Edge ends a session after a while; keep listening while the mode is on
      if (on) setTimeout(() => on && rec && safeStart(), fails ? 1000 * fails : 250);
    };
    safeStart();
    show("聞いています…");
  }
  function safeStart() { try { rec.start(); } catch {} }

  function stop() {
    on = false;
    clearTimeout(timer); timer = null; heard = "";
    if (rec) { const r = rec; rec = null; r.onend = null; try { r.abort(); } catch {} }
    show("");
  }

  // Edge's speech recognition never asks for the microphone itself: without permission it just
  // fails with "not-allowed". Asking through getUserMedia shows the prompt; the stream is closed
  // at once, since recognition opens the microphone on its own.
  async function askMic() {
    if (!navigator.mediaDevices?.getUserMedia) return "";
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      s.getTracks().forEach((t) => t.stop());
      return "";
    } catch (e) {
      return e.name === "NotFoundError" ? "マイクが見つかりません"
        : "マイクが許可されていません。Edge の設定（Cookie とサイトのアクセス許可 → マイク）で 127.0.0.1 を許可してください";
    }
  }

  async function toggle() {
    if (on) return stop();
    on = true; fails = 0;
    channel?.postMessage("on");  // only one window listens
    show("マイクの許可を確認しています…");
    const problem = await askMic();
    if (!on) return;  // turned off while the prompt was open
    if (problem) { stop(); return show(problem); }
    start();
  }

  channel && (channel.onmessage = (e) => { if (e.data === "on" && on) stop(); });
  window.EsListen = {
    init() {
      $("#listen").onclick = toggle;
      if (!Rec) $("#listen").title = "この画面では音声認識を使えません";
      show("");
    },
  };
})();
