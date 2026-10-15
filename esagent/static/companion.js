// Es, the companion who works at the desk next to you: expressions, speech,
// a pomodoro timer, ambient sounds and なかよし度. Exposes window.Es for index.html.
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const store = {
    get(k, d) { try { const v = localStorage.getItem("es." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("es." + k, JSON.stringify(v)); } catch {} },
  };
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-EsAgent": "1" }, body: JSON.stringify(body || {}) });
    const d = await r.json(); if (!r.ok) throw Object.assign(new Error(d.error || r.statusText), { link: d.link || "" }); return d;
  }

  // ---- the bar above the chat: timer, sounds, なかよし度 and Es's latest line ----------
  // Es herself (the black cat) lives on the desktop now (pet.py); the timer runs on the server
  // so both share it and it keeps going when this window is closed.
  function buildScene() {
    $("#scene").innerHTML = `
      <div id="controls">
        <div class="pomo-main">
          <div id="pomo-mode"><span id="pomo-label">集中タイマー</span><span id="pomo-round"></span></div>
          <div id="pomo-time">25:00</div>
        </div>
        <div class="pomo-col">
          <div id="pomo-btns">
            <button id="pomo-start">開始</button>
            <button id="pomo-reset" class="ghost">リセット</button>
          </div>
          <select id="pomo-task" title="集中するタスク"><option value="">タスクを選ばない</option></select>
        </div>
        <div class="pomo-col">
          <div id="sounds">
            <button class="ghost" data-sound="rain" title="雨の音">雨</button>
            <button class="ghost" data-sound="wind" title="風の音">風</button>
            <input id="volume" type="range" min="0" max="1" step="0.05" title="音量">
          </div>
          <div class="bond-row">
            <div id="bond"></div>
            <div class="cat-col">
              <div class="minicat-row">
                <div id="minicat" title="Es"><div id="minicat-sprite"></div></div>
                <div id="minicat-text">…</div>
              </div>
              <label class="pet-toggle" title="デスクトップの黒猫を出す / 消す">
                <input type="checkbox" id="pet-enabled"> デスクトップに表示
              </label>
              <div class="voice-row">
                <label class="pet-toggle" title="Es の返事とひとことを VOICEVOX で読み上げる">
                  <input type="checkbox" id="voice-enabled"> 読み上げ
                </label>
                <input id="voice-volume" type="range" min="0" max="1" step="0.05" title="読み上げの音量">
                <button class="ghost" id="voice-test" title="試しに読み上げる">試聴</button>
                <button class="ghost" id="voice-stop" title="読み上げと待ち列を止める">停止</button>
              </div>
              <div id="voice-status" title="読み上げの音声：VOICEVOX（冥鳴ひまり）">VOICEVOX:冥鳴ひまり</div>
            </div>
          </div>
        </div>
      </div>
      <div id="esline" hidden><span class="who">Es</span><span id="esline-text"></span><span id="esline-btns"></span></div>`;
  }

  // ---- the small cat in the bar (mirrors the desktop cat) ------------------------------------
  // Sprite strips are 50x50 frames; the cat itself sits at x11-36, y16-32, shown here at 2x.
  const MINI_ANIMS = { Idle: [10, 1.2], Sleeping1: [1, 1], Sleeping2: [1, 1], Laying: [8, 1.2],
                       Stretching: [13, 1.6], Meow: [4, 0.6], Sitting: [1, 1], Itch: [2, 0.35] };
  const MOOD_ANIM = { idle: "Idle", focus: "Sleeping1", break: "Laying", stretch: "Stretching", thinking: "Sitting",
                      happy: "Meow", talk: "Meow", worried: "Itch", sleep: "Sleeping2" };
  let miniCat = 2, miniAnim = "", miniMood = "idle", miniOverride = null, miniUntil = 0;

  function setMini(mood) {
    if (mood) miniMood = mood;
    const m = miniOverride && Date.now() < miniUntil ? miniOverride : miniMood;
    const name = MOOD_ANIM[m] || "Idle";
    if (name === miniAnim) return;
    miniAnim = name;
    const [frames, secs] = MINI_ANIMS[name];
    const el = $("#minicat-sprite");
    el.style.backgroundImage = `url("${encodeURI(`/static/cat/pack/Cat-${miniCat}/Cat-${miniCat}-${name}.png`)}")`;
    el.style.setProperty("--frames", frames);
    el.style.animation = "none";
    void el.offsetWidth;
    el.style.animation = frames > 1 ? `minicat ${secs}s steps(${frames}) infinite` : "none";
  }
  function flashMini(mood, ms = 2500) { miniOverride = mood; miniUntil = Date.now() + ms; setMini(); setTimeout(() => setMini(), ms + 50); }

  // ---- Es's lines in this window ---------------------------------------------------------
  let lineTimer = null, lineKey = null;
  function say(text, opts = {}) {
    lineKey = opts.ukey || null;  // the question shown, so an answer in the chat can take it down (#171)
    speak(text, opts);
    record(text, opts);
    $("#minicat-text").textContent = text;
    $("#esline-text").textContent = text;
    $("#esline-btns").innerHTML = (opts.buttons || []).map((x, i) =>
      `<button class="${i ? "ghost" : ""}" data-i="${i}">${esc(x.label)}</button>`).join("");
    $("#esline-btns").querySelectorAll("button").forEach((b) => b.onclick = () => {
      $("#esline").hidden = true; opts.buttons[+b.dataset.i].onClick?.();
    });
    $("#esline").hidden = false;
    clearTimeout(lineTimer);
    if (!opts.buttons) lineTimer = setTimeout(() => ($("#esline").hidden = true), opts.ms || Math.max(4000, text.length * 160));
  }

  // Everything Es says here goes to the one utterance log (#139); a line about an event carries the
  // event's key, so the cat, other windows and the server record it only once. Replies to chat
  // messages are already stored and pass record: false.
  function record(text, opts) {
    if (opts.record === false || !text) return;
    post("/api/es/said", { text, source: opts.source || "window", kind: opts.kind || "feedback",
      ...(opts.ukey ? { key: opts.ukey } : {}) }).catch(() => {});
  }

  // ---- reading aloud (#44): the server plays every line once; this window only hands lines over ----
  let voice = { enabled: false };
  function speak(text, opts) {
    // Timer notices are read by the server itself; a hidden window leaves lines to the desktop cat.
    if (opts.voice === false || !voice.enabled || document.visibilityState !== "visible") return;
    post("/api/voice/say", { text, ...(opts.key ? { key: opts.key } : {}) }).catch(() => {});
  }
  const ENGINE = { off: "待機中", starting: "VOICEVOX を起動中…", ready: "準備OK", missing: "VOICEVOX が見つかりません", error: "VOICEVOX を使えません" };
  function renderVoice(v) {
    if (!v) return;
    voice = v;
    const box = $("#voice-enabled"), vol = $("#voice-volume");
    if (document.activeElement !== box) box.checked = !!v.enabled;
    if (document.activeElement !== vol) vol.value = v.volume ?? 0.8;
    const ready = v.engine === "ready" && v.device ? `準備OK（${v.device.toUpperCase()}）` : ENGINE[v.engine] || "";
    const state = v.speaking ? "読み上げ中" : v.enabled ? ready : "オフ";
    const el = $("#voice-status");
    el.textContent = `${v.credit || "VOICEVOX:冥鳴ひまり"} · ${state}${v.queued ? `（あと${v.queued}件）` : ""}`;
    el.title = v.error ? `直近のエラー: ${v.error}` : "読み上げの音声：VOICEVOX（冥鳴ひまり）";
    el.classList.toggle("error", !!v.error && v.enabled);
  }
  async function setVoice(body) {
    try { renderVoice(await post("/api/voice", body)); } catch { say("読み上げを切り替えられなかった…"); }
  }

  // ---- focus timer (state on the server) ---------------------------------------------------
  // The length comes from the server (focus_minutes, #248): while idle, remaining is the next session's length.
  let P = { mode: "idle", remaining: 25 * 60, paused: false, round: 0, task_id: null, task: "" };
  let pAt = performance.now(), lastEvent = null, tasks = [];

  const fmt = (ms) => { const s = Math.max(0, Math.ceil(ms / 1000)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
  const remainingMs = () => P.mode === "idle" || P.paused ? P.remaining * 1000
    : Math.max(0, P.remaining * 1000 - (performance.now() - pAt));

  function renderPomo() {
    const rem = remainingMs();
    $("#pomo-time").textContent = fmt(rem);
    $("#pomo-time").className = P.mode === "idle" ? "" : P.mode;
    $("#pomo-label").textContent = P.mode === "focus" ? (P.paused ? "一時停止中" : "集中中") : P.mode === "break" ? (P.paused ? "一時停止中" : "休憩中") : "集中タイマー";
    $("#pomo-round").textContent = P.round ? `${P.round}回目` : "";
    $("#pomo-start").textContent = P.mode === "idle" ? "開始" : P.paused ? "再開" : "一時停止";
    document.title = P.mode === "idle" ? "E.S. Agent" : `${fmt(rem)} ${P.mode === "focus" ? "集中" : "休憩"} – E.S. Agent`;
  }

  function setP(state) { if (state && state.mode) { P = state; pAt = performance.now(); renderPomo(); } }
  async function act(action, extra = {}) {
    try { setP(await post("/api/pomo", { action, ...extra })); } catch { say("タイマーを操作できなかった…"); }
  }
  const startFocus = () => act("start", { task_id: $("#pomo-task").value || null });
  const toggle = () => P.mode === "idle" ? startFocus() : act("toggle");
  const reset = () => act("reset");

  // What happened on the server (also seen by the desktop cat).
  async function pollEvents() {
    try {
      setP(await (await fetch("/api/pomo")).json());
      const pet = await (await fetch("/api/pet")).json();
      miniCat = pet.cat || 2;
      setMini(pet.mood);
      const box = $("#pet-enabled");
      if (document.activeElement !== box) box.checked = !!pet.enabled;
      renderVoice(pet.voice);
      const r = await (await fetch(`/api/events?after=${lastEvent ?? 0}`)).json();
      if (lastEvent === null) lastEvent = r.last;  // don't replay older events
      else for (const ev of r.events) { lastEvent = ev.id; onEvent(ev); }
    } catch {}
  }

  function onEvent(ev) {
    const embed = bgm.mode === "embed" && bgm.focusOnly;
    if (ev.kind === "focus_start") {
      if (embed && bgm.url) playEmbed();
      say(ev.task ? `「${ev.task}」、いこう。` : "集中タイム。がんばろ。", { ms: 3000, voice: false, kind: "notice", source: "timer", ukey: ev.key });
    } else if (ev.kind === "focus_end") {
      chime();
      flashMini("stretch", 1700);
      if (embed) pauseEmbed();
      const timer = { voice: false, source: "timer", ukey: ev.key };
      if (ev.leveled_up) say(`なかよし度が上がった！ Lv${ev.level}「${ev.level_name}」`, { ms: 7000, ...timer, kind: "notice" });
      else if (ev.task_id) say(`おつかれさま！「${ev.task}」、終わった？`, { ...timer, kind: "question", buttons: [
        { label: "終わった", onClick: async () => {
          // Cheer only when it really was completed; a read-only task (#182) says why (#249).
          try { await post(`/api/tasks/${ev.task_id}`, { status: "done" }); }
          catch (e) { return say(e.message || "完了にできなかった…", { ms: 6000 }); }
          say("やったね！"); window.EsHooks?.refresh?.(); } },
        { label: "まだ", onClick: () => say("了解。次の集中で進めよ。") }] });
      else say(ev.long ? "おつかれさま！4回目だから長めに休んでね。" : "おつかれさま！ちょっと休憩しよ。", { ...timer, kind: "notice" });
      window.EsHooks?.refresh?.();
    } else if (ev.kind === "break_end") {
      chime();
      say("休憩おわり。次、いく？", { voice: false, kind: "question", source: "timer", ukey: ev.key, buttons: [{ label: "集中する", onClick: startFocus }, { label: "あとで" }] });
    } else if (ev.kind === "question_answered") {
      // Answered in the chat or by voice: take the buttons down so it isn't answered twice (#171).
      if (ev.question && ev.question === lineKey) { $("#esline").hidden = true; $("#esline-btns").innerHTML = ""; }
    } else if (ev.kind === "pause" || ev.kind === "reset") {
      if (embed) pauseEmbed();
    } else if (ev.kind === "answered") {
      window.EsHooks?.refresh?.();
      say("待たせてごめんね、返事したよ。", { ms: 3500, key: `event:${ev.id}`, kind: "notice", source: "system", ukey: ev.key });
    } else if (ev.kind === "resume") {
      if (embed && P.mode === "focus" && bgm.url) playEmbed();
    }
  }

  // ---- sound: generated in the browser, no audio files ------------------------
  let ctx = null, master = null;
  const sounds = {};
  const soundOn = store.get("sounds", { rain: false, wind: false });

  function audio() {
    if (!ctx) {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      master = ctx.createGain(); master.gain.value = store.get("volume", 0.4); master.connect(ctx.destination);
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }
  function noiseBuffer(brown) {
    const c = audio(), len = c.sampleRate * 3, buf = c.createBuffer(1, len, c.sampleRate), d = buf.getChannelData(0);
    let last = 0;
    for (let i = 0; i < len; i++) {
      const w = Math.random() * 2 - 1;
      d[i] = brown ? (last = (last + 0.02 * w) / 1.02) * 3.5 : w;
    }
    return buf;
  }
  function makeSound(kind) {
    const c = audio(), src = c.createBufferSource(), g = c.createGain();
    src.buffer = noiseBuffer(kind === "wind"); src.loop = true;
    if (kind === "rain") {
      const hp = c.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 500;
      const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 7000;
      src.connect(hp).connect(lp).connect(g); g.gain.value = 0.35;
    } else {
      const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 420;
      const lfo = c.createOscillator(), depth = c.createGain();
      lfo.frequency.value = 0.12; depth.gain.value = 0.25; lfo.connect(depth).connect(g.gain); lfo.start();
      src.connect(lp).connect(g); g.gain.value = 0.45;
    }
    g.connect(master); src.start();
    return { stop() { try { src.stop(); } catch {} } };
  }
  function setSound(kind, on) {
    soundOn[kind] = on; store.set("sounds", soundOn);
    if (on && !sounds[kind]) sounds[kind] = makeSound(kind);
    if (!on && sounds[kind]) { sounds[kind].stop(); delete sounds[kind]; }
    document.querySelector(`[data-sound="${kind}"]`).classList.toggle("on", on);
  }
  function chime() {
    try {
      const c = audio();
      [880, 1175].forEach((f, i) => {
        const o = c.createOscillator(), g = c.createGain(), t = c.currentTime + i * 0.18;
        o.type = "sine"; o.frequency.value = f; g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.25, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.9);
        o.connect(g).connect(master); o.start(t); o.stop(t + 1);
      });
    } catch {}
  }

  // ---- BGM: YouTube's official embedded player --------------------------------
  // YouTube's terms require the player to stay visible, so it lives in the side panel
  // (about 200px tall) and is never hidden while playing.
  const bgm = { player: null, ready: false, loading: false, presets: [], pending: null,
                url: store.get("bgm.url", ""), focusOnly: store.get("bgm.focusOnly", true), greeted: false,
                // The URL selects the playback destination; saved manual modes are ignored.
                mode: "embed", winOpen: false, winStatus: "" };

  function parseYouTube(url) {
    try {
      const u = new URL(url.trim());
      if (!youtubeUrl(url)) return null;
      const list = u.searchParams.get("list") || "";
      // A channel's current live stream: /embed/live_stream?channel=UC… or /channel/UC…/live
      const ch = u.searchParams.get("channel") || (u.pathname.match(/\/channel\/(UC[\w-]+)\/live/) || [])[1];
      if (ch) return { id: "live_stream", channel: ch, list: "" };
      let id = u.searchParams.get("v") || "";
      if (u.hostname === "youtu.be") id = u.pathname.slice(1);
      const m = u.pathname.match(/\/(live|embed|shorts)\/([\w-]{6,})/);
      if (m) id = m[2];
      return id || list ? { id, list } : null;
    } catch { return null; }
  }

  function youtubeUrl(url) {
    try {
      const u = new URL(url.trim());
      return ["https:", "http:"].includes(u.protocol) && !u.username && !u.password
        && ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"].includes(u.hostname) ? u : null;
    } catch { return null; }
  }

  function playbackMode(url) {
    const u = youtubeUrl(url);
    if (!u) return null;
    if (u.hostname === "music.youtube.com") return "window";
    const video = parseYouTube(url);
    return video ? video.channel ? "window" : "embed" : null;
  }

  function buildBgm() {
    $("#bgm").innerHTML = `
      <div class="bgm-heading">
      <h2 id="bgm-toggle" title="クリックで開く / 閉じる（閉じると一時停止）"><span class="caret">▾</span> YouTube・BGM</h2>
      <button id="yt-open" class="ghost" title="動画検索と自分のプレイリスト">動画・プレイリスト</button>
      </div>
      <div id="bgm-body">
      <div id="yt-library">
        <div class="bgm-row"><button id="yt-connect" class="ghost">GoogleでYouTubeに接続</button></div>
        <p id="yt-auth-status">接続状態を確認しています…</p>
        <form id="yt-search" class="bgm-row">
          <input id="yt-query" maxlength="200" placeholder="動画を検索" aria-label="YouTubeで検索する言葉">
          <button id="yt-search-button" class="ghost" type="submit" disabled>検索</button>
        </form>
        <div class="bgm-row"><button id="yt-playlists" class="ghost" disabled>自分のプレイリスト</button></div>
        <p id="yt-status" role="status" aria-live="polite"></p>
        <div id="yt-results" aria-label="YouTubeの一覧"></div>
        <div id="yt-pages" class="bgm-row" hidden>
          <button id="yt-prev" class="ghost">前のページ</button><button id="yt-next" class="ghost">次のページ</button>
        </div>
        <p class="yt-note">Google連携でプレイリストを表示できます。再生できない動画はYouTubeで開いてください。
          <a href="/youtube-setup" target="_blank" rel="noopener">接続の設定・再生の制限</a></p>
      </div>
      <div id="bgm-frame"><div id="bgm-player"></div><div id="bgm-empty">好きな lo-fi の YouTube URL を<br>下に貼って Enter</div></div>
      <div id="bgm-win">
        <div id="bgm-win-status">別ウィンドウで再生します（Edge のログインがそのまま使えます）。<br>最小化しても流れ続けます。</div>
        <button id="bgm-win-open" class="ghost">YouTubeで開く</button>
        <button id="bgm-win-return" class="ghost">アプリ内再生に戻る</button>
      </div>
      <div class="bgm-row">
        <select id="bgm-select" title="プリセット"></select>
        <button id="bgm-play" class="ghost" title="再生 / 一時停止">▶</button>
      </div>
      <div class="bgm-row">
        <input id="bgm-url" placeholder="YouTube の URL を貼って Enter">
      </div>
      <label class="bgm-opt"><input type="checkbox" id="bgm-focus"> 集中中だけ流す（休憩で一時停止）</label>
      </div>`;
    buildYouTube();
    setBgmOpen(store.get("bgm.open", true));
    $("#bgm-toggle").onclick = () => setBgmOpen($("#bgm").classList.contains("closed"));
    $("#bgm-focus").checked = bgm.focusOnly;
    $("#bgm-win-open").onclick = () => openWindow();
    setMode(playbackMode(bgm.url) || "embed");
    $("#bgm-win-return").onclick = () => useEmbedded(bgm.url);
    $("#bgm-focus").onchange = (e) => { bgm.focusOnly = e.target.checked; store.set("bgm.focusOnly", bgm.focusOnly); };
    $("#bgm-play").onclick = () => isPlaying() ? pauseBgm() : playBgm();
    $("#bgm-select").onchange = (e) => {
      if (!e.target.value) return;
      return selectBgm(e.target.value);
    };
    $("#bgm-url").onkeydown = (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      if (!playbackMode(e.target.value)) return say("その URL は YouTube または YouTube Music のリンクとして読めなかったよ。");
      const url = e.target.value; e.target.value = "";
      return selectBgm(url);
    };
  }

  const yt = { connected: false, client: false, busy: false, kind: "playlists", query: "", playlist: "", title: "", next: "", previous: "" };

  function updateYouTube(auth) {
    const disconnected = yt.connected && !auth.connected;
    yt.connected = !!auth.connected; yt.client = !!auth.client;
    $("#yt-connect").textContent = yt.connected ? "Googleに再接続" : "GoogleでYouTubeに接続";
    $("#yt-auth-status").textContent = yt.connected ? "接続済み · プレイリストを開くか、動画を検索できます。"
      : yt.client ? "接続すると通常のブラウザでGoogleの確認画面が開きます。" : "Google接続の設定が必要です。下の「接続の設定」を開いてください。";
    $("#yt-connect").disabled = yt.busy || !yt.client;
    $("#yt-search-button").disabled = yt.busy || !yt.connected;
    $("#yt-playlists").disabled = yt.busy || !yt.connected;
    $("#yt-query").disabled = yt.busy || !yt.connected;
    if (disconnected) {
      $("#yt-results").replaceChildren(); $("#yt-pages").hidden = true;
      $("#yt-status").textContent = "Googleへの接続が切れました。再接続してください。";
    }
  }

  function buildYouTube() {
    const dialog = document.createElement("dialog"); dialog.id = "yt-dialog";
    const header = document.createElement("div"); header.className = "yt-dialog-header";
    header.innerHTML = '<h2>YouTube</h2><button class="ghost" type="button" aria-label="YouTubeの一覧を閉じる">閉じる</button>';
    header.querySelector("button").onclick = () => dialog.close();
    dialog.append(header, $("#yt-library")); document.body.append(dialog);
    $("#yt-open").onclick = () => { if (bgm.mode === "embed") pauseEmbed(); dialog.showModal(); };
    $("#yt-connect").onclick = async () => {
      yt.busy = true; updateYouTube(yt);
      try {
        await post("/api/youtube/connect");
        $("#yt-status").textContent = "ブラウザで読み取りを許可してから、ここに戻ってください。";
      } catch (e) { $("#yt-status").textContent = e.message; }
      finally { yt.busy = false; updateYouTube(yt); }
    };
    $("#yt-search").onsubmit = (e) => {
      e.preventDefault();
      const query = $("#yt-query").value.trim();
      if (!query) { $("#yt-status").textContent = "検索する言葉を入力してください。"; return; }
      loadYouTube("search", { query });
    };
    $("#yt-playlists").onclick = () => loadYouTube("playlists");
    $("#yt-prev").onclick = () => loadYouTube(yt.kind, { query: yt.query, playlist: yt.playlist, title: yt.title }, yt.previous);
    $("#yt-next").onclick = () => loadYouTube(yt.kind, { query: yt.query, playlist: yt.playlist, title: yt.title }, yt.next);
  }

  async function loadYouTube(kind, options = {}, page = "") {
    if (yt.busy || !yt.connected) return;
    yt.busy = true; updateYouTube(yt);
    $("#yt-status").textContent = "読み込んでいます…";
    $("#yt-results").replaceChildren(); $("#yt-pages").hidden = true;
    try {
      const data = await post(`/api/youtube/${kind}`, { query: options.query || "", playlist: options.playlist || "", page });
      // Discard private library results if the connection changed during the request.
      if (!yt.connected) return;
      Object.assign(yt, { kind, query: options.query || "", playlist: options.playlist || "", title: options.title || "",
                         next: data.next || "", previous: data.previous || "" });
      const items = data.items || [];
      $("#yt-status").textContent = (kind === "items" ? `${yt.title} · ` : kind === "search" ? `「${yt.query}」 · ` : "プレイリスト · ")
        + (items.length ? `${items.length}件` : "表示できる項目がありません。")
        + (data.unavailable ? `（閲覧できない動画 ${data.unavailable}件）` : "");
      const container = $("#yt-results");
      for (const item of items) {
        const button = document.createElement("button"); button.type = "button"; button.className = "yt-item ghost";
        if (kind !== "playlists" && item.thumbnail) {
          const img = document.createElement("img"); img.src = item.thumbnail; img.alt = ""; img.loading = "lazy";
          button.append(img);
        }
        const text = document.createElement("span");
        const title = document.createElement("strong"); title.textContent = item.title; text.append(title);
        const detail = document.createElement("small"); detail.textContent = kind === "playlists" ? `${item.count}本の動画` : item.channel;
        text.append(detail); button.append(text);
        button.onclick = () => kind === "playlists" ? loadYouTube("items", { playlist: item.id, title: item.title }) : playYouTubeVideo(item.id);
        container.append(button);
      }
      $("#yt-pages").hidden = !yt.next && !yt.previous;
      $("#yt-prev").disabled = !yt.previous; $("#yt-next").disabled = !yt.next;
    } catch (e) {
      $("#yt-status").textContent = e.message;
      // The page that enables the API (#86): the server passes only Google's own console link.
      if (e.link && /^https:\/\/console\.(developers|cloud)\.google\.com\/apis\//.test(e.link)) {
        const a = document.createElement("a");
        a.href = e.link; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = "YouTube Data API を有効にするページを開く";
        $("#yt-status").append(document.createElement("br"), a);
      }
    }
    finally { yt.busy = false; updateYouTube(yt); }
  }

  async function selectBgm(url) {
    const mode = playbackMode(url);
    if (!mode) return say("YouTube または YouTube Music のリンクを選んでね。");
    if (mode === "embed") return useEmbedded(url);
    if (bgm.mode === "window") await media("pause");
    setMode(mode); setBgmOpen(true); setUrl(url.trim());
    return openWindow();
  }

  async function useEmbedded(url) {
    const u = youtubeUrl(url);
    if (!u || !parseYouTube(url)) return say("アプリ内で再生する動画のリンクを選んでね。");
    if (u.hostname === "music.youtube.com") u.hostname = "www.youtube.com";
    if (bgm.mode === "window") await media("pause");
    setMode("embed"); setBgmOpen(true);
    setUrl(u.href);
    playBgm();
  }

  function playYouTubeVideo(id) {
    $("#yt-dialog").close();
    return useEmbedded(`https://www.youtube.com/watch?v=${encodeURIComponent(id)}`);
  }

  function setMode(mode) {
    if (mode !== bgm.mode && bgm.mode === "embed") pauseEmbed();
    bgm.mode = mode; store.set("bgm.mode", mode);
    reportPlayback();
    $("#bgm").classList.toggle("winmode", mode !== "embed");
    $("#bgm-win-open").textContent = youtubeUrl(bgm.url)?.hostname === "music.youtube.com" ? "YouTube Musicで開く" : "YouTubeで開く";
    $("#bgm-win-return").disabled = !parseYouTube(bgm.url);
    if (mode !== "embed") refreshWin();
    syncBgm();
  }

  async function media(action) {
    try {
      const r = await post("/api/media", { action });
      const s = r.sessions || [];
      bgm.winStatus = s.some((x) => x.status === "Playing") ? "Playing" : s.length ? "Paused" : "";
      const title = s[0]?.title;
      $("#bgm-win-status").innerHTML = s.length
        ? `${bgm.winStatus === "Playing" ? "▶ 再生中" : "⏸ 一時停止中"}<br><small>${esc(title || "YouTube")}</small>`
        : `${youtubeUrl(bgm.url)?.hostname === "music.youtube.com" ? "YouTube Music" : "YouTube"} のウィンドウは開いていません。`;
      if (bgm.mode === "window") $("#bgm-play").textContent = bgm.winStatus === "Playing" ? "⏸" : "▶";
      // remember a URL the first time we learn its title
      if (title && bgm.mode === "window" && bgm.url && !bgm.presets.some((p) => p.url === bgm.url)) {
        post("/api/bgm", { url: bgm.url, title }).then((x) => { bgm.presets = x.bgm; renderPresets(); }).catch(() => {});
      }
      return s;
    } catch (e) { return []; }
  }
  const refreshWin = () => media("status");

  async function openWindow() {
    if (!bgm.url) {
      const first = bgm.presets[0];
      if (!first) { $("#bgm-url").focus(); return say("流したい YouTube の URL を、BGM の欄に貼ってね。"); }
      setUrl(first.url);
    }
    const target = youtubeUrl(bgm.url);
    if (!target || !playbackMode(bgm.url)) return say("YouTube のリンクを選んでね。");
    const channel = parseYouTube(bgm.url)?.channel;
    if (channel) { target.hostname = "www.youtube.com"; target.pathname = `/channel/${channel}/live`; target.search = ""; }
    try { await post("/api/bgm/window", { url: target.href }); }
    catch (e) { return say(`ウィンドウを開けませんでした。${e.message || "もう一度試してください。"}`); }
    bgm.winOpen = true;
    say(`${target.hostname === "music.youtube.com" ? "YouTube Music" : "YouTube"} のウィンドウを開いたよ。`, { ms: 3500 });
    setTimeout(refreshWin, 6000);
  }

  function setBgmOpen(open) {
    // YouTube's player must stay visible while it plays, so closing the panel pauses it.
    if (!open && bgm.mode === "embed") pauseEmbed();
    $("#bgm").classList.toggle("closed", !open);
    store.set("bgm.open", open);
  }

  function renderPresets() {
    const sel = $("#bgm-select");
    const opts = bgm.presets.map((p) => `<option value="${esc(p.url)}">${esc(p.title)}</option>`);
    if (bgm.url && !bgm.presets.some((p) => p.url === bgm.url)) opts.push(`<option value="${esc(bgm.url)}">貼り付けた URL</option>`);
    sel.innerHTML = `<option value="">BGM を選ぶ</option>` + opts.join("");
    sel.value = bgm.url || "";
  }

  // The most recently opened/focused app owns an ephemeral report lease.
  let playbackToken = "", playbackSequence = 0, playbackGeneration = 0, playbackError = false, playbackSelectionPending = false;
  async function claimPlayback() {
    const generation = ++playbackGeneration;
    playbackToken = "";
    try {
      const report = await post("/api/playback/open", {});
      if (generation !== playbackGeneration) return;
      playbackToken = report.token; playbackSequence = 0;
      reportPlayback();
    } catch {}
  }
  function reportPlayback() {
    if (!playbackToken) return;
    let data = {}, code = -1;
    try {
      if (bgm.ready) { data = bgm.player.getVideoData?.() || {}; code = bgm.player.getPlayerState?.(); }
    } catch {}
    const chosen = parseYouTube(bgm.url);
    if (playbackSelectionPending || (chosen?.id && data.video_id && chosen.id !== data.video_id)) { data = {}; code = -1; }
    const state = bgm.mode !== "embed" ? "external" : !bgm.url ? "none" : playbackError ? "error"
      : ({ 1: "playing", 2: "paused", 0: "ended", 3: "buffering" })[code] || "selected";
    post("/api/playback/state", { token: playbackToken, sequence: ++playbackSequence, state,
      title: bgm.mode === "embed" ? data.title || "" : "",
      channel: bgm.mode === "embed" ? data.author || "" : "",
      video_id: bgm.mode === "embed" ? data.video_id || parseYouTube(bgm.url)?.id || "" : ""
    }).catch(() => {});
  }

  function setUrl(url) {
    playbackError = false; playbackSelectionPending = true;
    bgm.url = url; store.set("bgm.url", url); renderPresets();
    reportPlayback();
    $("#bgm-win-open").textContent = youtubeUrl(url)?.hostname === "music.youtube.com" ? "YouTube Musicで開く" : "YouTubeで開く";
    $("#bgm-win-return").disabled = !parseYouTube(url);
    const v = parseYouTube(url);
    if (bgm.mode === "embed" && bgm.ready && v) {
      if (v.channel) {  // the API cannot switch to a channel stream in place, so rebuild the player
        bgm.player.destroy(); bgm.ready = false; bgm.loading = false;
        $("#bgm-frame").innerHTML = '<div id="bgm-player"></div><div id="bgm-empty"></div>';
        return;
      }
      if (v.list) bgm.player.loadPlaylist({ list: v.list, listType: "playlist" });
      else bgm.player.loadVideoById(v.id);
    }
  }

  function loadApi(then) {
    if (bgm.ready) return then();
    bgm.pending = then;
    if (bgm.loading) return;
    bgm.loading = true;
    const create = () => {
      const v = parseYouTube(bgm.url) || {};
      $("#bgm-empty")?.remove();
      bgm.player = new YT.Player("bgm-player", {
        width: "100%", height: "100%", videoId: v.id || undefined,
        playerVars: { playsinline: 1, rel: 0, origin: location.origin, ...(v.list ? { listType: "playlist", list: v.list } : {}),
                      ...(v.channel ? { channel: v.channel } : {}) },
        events: {
          onReady: () => {
            bgm.ready = true; bgm.player.setVolume(Math.round(store.get("volume", 0.4) * 100));
            const latest = parseYouTube(bgm.url);
            if (latest && !latest.channel && (latest.id !== v.id || latest.list !== v.list)) setUrl(bgm.url);
            const pending = bgm.pending; bgm.pending = null;
            if (embedVisible()) pending?.(); else pauseEmbed();
          },
          onStateChange: () => {
            playbackSelectionPending = false;
            playbackError = false;
            reportPlayback();
            if (bgm.player.getPlayerState?.() === 1 && !embedVisible()) { pauseEmbed(); return; }
            $("#bgm-play").textContent = isPlaying() ? "⏸" : "▶";
            // Remember a pasted URL once it really plays, with its title.
            if (isPlaying() && bgm.url && !bgm.presets.some((p) => p.url === bgm.url)) {
              const title = bgm.player.getVideoData?.().title || "YouTube";
              post("/api/bgm", { url: bgm.url, title }).then((r) => { bgm.presets = r.bgm; renderPresets(); }).catch(() => {});
            }
          },
          onError: (e) => {
            playbackError = true; reportPlayback();
            const url = bgm.url;
            say(e.data === 101 || e.data === 150
              ? "この動画はアプリ内での再生が許可されていません。"
              : e.data === 153 ? "YouTubeが再生元を確認できませんでした。YouTubeで開いてください。"
              : "この動画を再生できませんでした。ログインや年齢確認が必要な場合はYouTubeで開いてください。",
              { buttons: [{ label: "YouTubeで開く", onClick: () => {
                  setMode("window"); return openWindow(); } }, { label: "閉じる" }] });
          },
        },
      });
    };
    if (window.YT && window.YT.Player) return create();  // script already loaded (player rebuilt)
    window.onYouTubeIframeAPIReady = create;
    const tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    tag.onerror = () => { bgm.loading = false; say("YouTube に接続できなかった。オフラインかも。"); };
    document.head.appendChild(tag);
  }

  const isPlaying = () => bgm.mode !== "embed" ? bgm.winStatus === "Playing"
                                                 : bgm.ready && bgm.player.getPlayerState?.() === 1;

  async function playBgm() {
    if (bgm.mode === "embed" && $("#bgm").classList.contains("closed")) setBgmOpen(true);
    if (bgm.mode !== "embed") {
      const sessions = await media("play");
      if (!sessions.length) openWindow();  // nothing to resume yet: open the window
      return;
    }
    playEmbed();
  }
  function pauseBgm() { if (bgm.mode !== "embed") media("pause"); else pauseEmbed(); }

  function playEmbed() {
    if (!embedVisible()) return;
    if (!bgm.url) {
      const first = bgm.presets[0];
      if (!first) { $("#bgm-url").focus(); return say("好きな lo-fi の YouTube URL を、BGM の欄に貼ってね。"); }
      return selectBgm(first.url);
    }
    loadApi(() => {
      if (!embedVisible()) return;
      bgm.player.playVideo();
      if (!bgm.greeted) { bgm.greeted = true; say(pick(["BGM かけるね。", "いい感じの曲、流しとくね。"]), { ms: 2500, kind: "notice", source: "bgm" }); }
    });
  }
  function embedVisible() { return bgm.mode === "embed" && !$("#bgm").classList.contains("closed") && !$("#yt-dialog").open; }
  function pauseEmbed() { bgm.pending = null; if (bgm.ready) bgm.player.pauseVideo(); }

  // ---- なかよし度 ------------------------------------------------------------
  function renderBond(c) {
    if (!c) return;
    const next = c.to_next ? `あと${c.to_next}回で次のレベル` : "最高レベル";
    $("#bond").innerHTML = `なかよし度 <b>Lv${c.level} ${esc(c.level_name)}</b><br>今日 ${c.today}回 ・ 合計 ${c.sessions}回 ・ ${next}`;
  }

  // ---- public hooks used by index.html ---------------------------------------------
  function syncBgm() { post("/api/bgm/sync", { mode: bgm.mode, focusOnly: bgm.focusOnly }).catch(() => {}); }

  window.Es = {
    init() {
      buildScene();
      $("#pomo-start").onclick = toggle;
      $("#pomo-reset").onclick = reset;
      $("#pomo-task").onchange = (e) => act("task", { task_id: e.target.value || null });
      $("#volume").value = store.get("volume", 0.4);
      $("#volume").oninput = (e) => {
        store.set("volume", +e.target.value);
        if (master) master.gain.value = +e.target.value;
        if (bgm.ready) bgm.player.setVolume(Math.round(+e.target.value * 100));
      };
      buildBgm();
      syncBgm();
      claimPlayback();
      window.addEventListener("focus", claimPlayback);
      setInterval(reportPlayback, 5000);
      $("#pet-enabled").onchange = async (e) => {
        try {
          await post("/api/pet/enabled", { enabled: e.target.checked });
          say(e.target.checked ? "デスクトップに出るね。" : "デスクトップからは消えるね。ここにはいるよ。", { ms: 3000 });
        } catch { e.target.checked = !e.target.checked; say("切り替えられなかった…"); }
      };
      $("#voice-enabled").onchange = (e) => setVoice({ enabled: e.target.checked });
      $("#voice-volume").onchange = (e) => setVoice({ volume: +e.target.value });
      $("#voice-test").onclick = () => post("/api/voice/test", {}).catch(() => say("試聴できなかった…", { voice: false }));
      $("#voice-stop").onclick = () => post("/api/voice/stop", {}).then(renderVoice).catch(() => {});
      $("#minicat").onclick = () => { flashMini("talk", 1500); say(pick(["にゃ？", "なになに？", "にゃー"]), { ms: 2000, kind: "murmur" }); };
      setMini("idle");
      $("#bgm-focus").addEventListener("change", syncBgm);
      document.querySelectorAll("[data-sound]").forEach((b) => b.onclick = () => setSound(b.dataset.sound, !soundOn[b.dataset.sound]));
      // Browsers only allow audio after a click, so restore saved sounds on the first one.
      document.addEventListener("click", () => Object.entries(soundOn).forEach(([k, v]) => v && !sounds[k] && setSound(k, true)), { once: true });
      Object.entries(soundOn).forEach(([k, v]) => document.querySelector(`[data-sound="${k}"]`).classList.toggle("on", v));
      setInterval(renderPomo, 250);
      setInterval(pollEvents, 1500);
      pollEvents();
    },
    update(S) {
      updateYouTube(S.youtube || {});
      tasks = S.tasks || [];
      if (JSON.stringify(bgm.presets) !== JSON.stringify(S.bgm || [])) { bgm.presets = S.bgm || []; renderPresets(); }
      const sel = $("#pomo-task");
      if (document.activeElement !== sel) {
        sel.innerHTML = `<option value="">タスクを選ばない</option>` +
          tasks.filter((t) => t.status !== "delegated").map((t) => `<option value="${t.id}">${esc(t.title)}</option>`).join("");
        sel.value = tasks.some((t) => t.id === P.task_id) ? String(P.task_id) : "";
      }
      renderBond(S.companion);
    },
    thinking() {},  // the desktop cat shows this now
    focusOn(taskId) {
      if (P.mode === "idle") act("start", { task_id: taskId });
      else { act("task", { task_id: taskId }); say("今の時間が終わったら、そのタスクで始めよ。"); }
    },
    react(result) {
      const ev = (result && result.events) || [];
      if (ev.some((e) => e.startsWith("✔"))) { flashMini("happy"); say(pick(["やったね！", "えらい！その調子。", "ひとつ片づいたね。"]), { ms: 3000, record: false }); }
      else if (ev.some((e) => e.startsWith("→"))) say("Claude に頼んでおくね。右のボタンから開けるよ。", { ms: 3500, record: false });  // the server records the event's line
    },
    say,
  };
})();
