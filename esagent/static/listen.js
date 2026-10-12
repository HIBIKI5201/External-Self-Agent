// 会話モード (#89): while it is on, this window listens with Edge's built-in speech recognition
// (Web Speech API; the audio goes to Microsoft's speech service), and sends what you said as an
// ordinary chat message once you stop talking. Es's voice comes out of Windows, not this page, so
// the browser cannot cancel it: a recognised line that matches what Es is saying is dropped as an
// echo; anything else is you, and stops Es's reading (#44). Nothing is recorded or kept.
(() => {
  // How long a pause ends what you are saying (#116, config listen_silence_ms / listen_incomplete_ms):
  // longer when the words so far stop in the middle of a sentence (「〜を」「〜とかの」「〜けど」).
  let timing = { silence: 1200, incomplete: 3000 };
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

  // Particles and connectives a sentence does not end with. 「〜して」「〜なの」 can end a request or a
  // question, so て and a lone の are not on the list.
  const MIDWAY = /(?:[をがにはでとへもや]|って|から|けど|けれど|けども|とか|とかの|ので|のに|たら|ば|し|ながら|[、,])$/;
  function silenceFor(text, t = timing) {
    const s = String(text || "").trim().replace(/[。.．\s]+$/, "");
    return s && MIDWAY.test(s) ? t.incomplete : t.silence;
  }

  if (typeof module !== "undefined") { module.exports = { isEcho, words, norm, silenceFor }; return; }

  // ---- the page -----------------------------------------------------------------------
  const $ = (s) => document.querySelector(s);
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const channel = "BroadcastChannel" in window ? new BroadcastChannel("es-listen") : null;
  let rec = null, on = false, heard = "", timer = null, restart = null, epoch = 0;
  let fails = 0, barged = false, checking = false;
  // The microphone Edge listens with (#93): the Windows default input device, named by the track the
  // permission check opened. Shown only on this screen; never stored or sent.
  let mic = "", lastHeard = 0, quiet = null;
  const QUIET_MS = 20000;  // nothing recognised for this long: point at the Windows input device

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
    const which = on && /^聞いています/.test(state) ? `（マイク：${mic || "不明"}）` : "";
    line.textContent = text ? `${state}${text}` : state + which;
  }

  function watchQuiet(turn) {
    clearTimeout(quiet);
    quiet = setTimeout(() => {
      if (!on || turn !== epoch) return;
      if (Date.now() - lastHeard >= QUIET_MS && !heard) {
        show("聞いています…");
        $("#listen-line").textContent += " 何も聞き取れていません。Windows の既定の入力デバイス（設定 → システム → サウンド → 入力）が話しているマイクか確認してください。";
      }
      watchQuiet(turn);
    }, QUIET_MS);
  }

  let warmedAt = 0;
  async function heardSome(text) {
    // Speech has started: load the model now, before the sentence ends (#119).
    if (Date.now() - warmedAt > 20000) { warmedAt = Date.now(); post("/api/warm").catch(() => {}); }
    // The first non-echo words of what you are saying stop Es's reading (barge-in).
    if (barged || checking || !text.trim()) return;
    checking = true;  // results keep arriving while Es's lines are fetched
    const turn = epoch;
    try {
      if (isEcho(text, await esLines())) return;
    } finally {
      if (turn === epoch) checking = false;
    }
    if (!on || turn !== epoch) return;
    barged = true;
    post("/api/voice/stop").catch(() => {});
  }

  async function finish() {
    timer = null;
    const text = heard.trim();
    heard = ""; barged = false;
    if (!on) return;
    if (!text) return show("聞いています…");
    const turn = epoch;
    const echo = isEcho(text, await esLines());
    if (!on || turn !== epoch) return;
    if (echo) return show("聞いています…（Es の声は送りません）");
    show("送信：", text);
    window.EsHooks?.send?.(text, null, { via: "voice" });
    setTimeout(() => on && turn === epoch && show("聞いています…"), 1500);
  }

  function retry() {
    if (!on || restart !== null) return;
    const turn = epoch;
    restart = setTimeout(() => {
      restart = null;
      if (on && turn === epoch) start();
    }, fails ? 1000 * fails : 250);
  }

  function start() {
    if (!Rec) {
      on = false;
      return show("この画面では音声認識を使えません（Edge で開いてください）");
    }
    const turn = epoch;
    let r, done = 0; // results belong to this recognition session, starting at index zero
    const current = () => on && turn === epoch && rec === r;
    try { r = new Rec(); } catch { return startFailed(); }
    rec = r;
    r.lang = "ja-JP";
    r.continuous = true;
    r.interimResults = true;
    r.onresult = (e) => {
      if (!current()) return;
      fails = 0;
      lastHeard = Date.now();
      const w = words(e.results, done);
      for (let i = done; i < e.results.length; i++) if (e.results[i].isFinal) done = i + 1;
      heard += w.final;
      const now = (heard + w.interim).trim();
      if (!now) return;
      show("認識中：", now);
      heardSome(now);
      clearTimeout(timer);
      timer = setTimeout(finish, silenceFor(now));
    };
    r.onerror = (e) => {
      if (!current()) return;
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        stop(); return show("マイクが許可されていません。Edge の設定（Cookie とサイトのアクセス許可 → マイク）で 127.0.0.1 を許可してください");
      }
      if (e.error === "audio-capture") { stop(); return show("マイクが見つかりません"); }
      if (e.error === "network" && ++fails >= MAX_FAILS) { stop(); return show("音声認識のサービスにつながりません"); }
      if (!["no-speech", "aborted", "network"].includes(e.error)) show(`音声認識のエラー（${e.error}）`);
    };
    r.onend = () => {  // Every restart gets a fresh session and result cursor.
      if (!current()) return;
      rec = null;
      retry();
    };
    try { r.start(); } catch { return startFailed(); }
    if (current()) show("聞いています…");
  }

  function startFailed() {
    if (rec) { const r = rec; rec = null; try { r.abort(); } catch {} }
    if (++fails >= MAX_FAILS) { stop(); return show("音声認識を開始できません。会話モードをもう一度オンにしてください"); }
    show("音声認識を再開しています…");
    retry();
  }

  function stop() {
    on = false;
    epoch++;
    clearTimeout(restart); restart = null;
    clearTimeout(timer); timer = null; heard = "";
    clearTimeout(quiet); quiet = null;
    barged = false; checking = false;
    if (rec) { const r = rec; rec = null; r.onend = null; try { r.abort(); } catch {} }
    show("");
  }

  // Edge's speech recognition never asks for the microphone itself: without permission it just
  // fails with "not-allowed". Asking through getUserMedia shows the prompt; the stream is closed
  // at once, since recognition opens the microphone on its own.
  async function askMic() {
    mic = "";
    if (!navigator.mediaDevices?.getUserMedia) return "";
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      mic = (s.getAudioTracks?.()[0]?.label || "").trim();  // the default input device
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
    const turn = ++epoch;
    channel?.postMessage("on");  // only one window listens
    fetch("/api/voice").then(r => r.json()).then(v => { if (v.listen) timing = v.listen; }).catch(() => {});
    show("マイクの許可を確認しています…");
    const problem = await askMic();
    if (!on || turn !== epoch) return;  // turned off or restarted while permission was pending
    if (problem) { stop(); return show(problem); }
    lastHeard = Date.now();
    watchQuiet(turn);
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
