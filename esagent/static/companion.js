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
    const d = await r.json(); if (!r.ok) throw new Error(d.error || r.statusText); return d;
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
          <div id="bond"></div>
        </div>
      </div>
      <div id="esline" hidden><span class="who">Es</span><span id="esline-text"></span><span id="esline-btns"></span></div>`;
  }

  // ---- Es's lines in this window ---------------------------------------------------------
  let lineTimer = null;
  function say(text, opts = {}) {
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

  // ---- focus timer (state on the server) ---------------------------------------------------
  const FOCUS = 25;
  let P = { mode: "idle", remaining: FOCUS * 60, paused: false, round: 0, task_id: null, task: "" };
  let pAt = performance.now(), lastEvent = null, tasks = [];

  const fmt = (ms) => { const s = Math.max(0, Math.ceil(ms / 1000)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
  const remainingMs = () => P.mode === "idle" ? FOCUS * 60000
    : P.paused ? P.remaining * 1000 : Math.max(0, P.remaining * 1000 - (performance.now() - pAt));

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
      const r = await (await fetch(`/api/events?after=${lastEvent ?? 0}`)).json();
      if (lastEvent === null) lastEvent = r.last;  // don't replay older events
      else for (const ev of r.events) { lastEvent = ev.id; onEvent(ev); }
    } catch {}
  }

  function onEvent(ev) {
    const embed = bgm.mode === "embed" && bgm.focusOnly;
    if (ev.kind === "focus_start") {
      if (embed && bgm.url) playEmbed();
      say(ev.task ? `「${ev.task}」、いこう。` : "集中タイム。がんばろ。", { ms: 3000 });
    } else if (ev.kind === "focus_end") {
      chime();
      if (embed) pauseEmbed();
      if (ev.leveled_up) say(`なかよし度が上がった！ Lv${ev.level}「${ev.level_name}」`, { ms: 7000 });
      else if (ev.task_id) say(`おつかれさま！「${ev.task}」、終わった？`, { buttons: [
        { label: "終わった", onClick: async () => { await post(`/api/tasks/${ev.task_id}`, { status: "done" }); say("やったね！"); window.EsHooks?.refresh?.(); } },
        { label: "まだ", onClick: () => say("了解。次の25分で進めよ。") }] });
      else say(ev.long ? "おつかれさま！4回目だから長めに休んでね。" : "おつかれさま！ちょっと休憩しよ。");
      window.EsHooks?.refresh?.();
    } else if (ev.kind === "break_end") {
      chime();
      say("休憩おわり。次、いく？", { buttons: [{ label: "集中する", onClick: startFocus }, { label: "あとで" }] });
    } else if (ev.kind === "pause" || ev.kind === "reset") {
      if (embed) pauseEmbed();
    } else if (ev.kind === "answered") {
      window.EsHooks?.refresh?.();
      say("待たせてごめんね、返事したよ。", { ms: 3500 });
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
                // "embed": player inside EsAgent. "window": YouTube's own site in a small Edge window,
                // where the user's Premium login applies and non-embeddable streams (Lofi Girl) play.
                mode: store.get("bgm.mode", "embed"), winOpen: false, winStatus: "" };

  function parseYouTube(url) {
    try {
      const u = new URL(url.trim());
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

  function buildBgm() {
    $("#bgm").innerHTML = `
      <h2 id="bgm-toggle" title="クリックで開く / 閉じる（閉じると一時停止）"><span class="caret">▾</span> BGM</h2>
      <div id="bgm-body">
      <div class="bgm-modes">
        <label><input type="radio" name="bgm-mode" value="embed"> この中で再生</label>
        <label><input type="radio" name="bgm-mode" value="window"> YouTube（動画）— ログイン・Premium で再生</label>
        <label><input type="radio" name="bgm-mode" value="music"> YouTube Music（音楽）— ライブラリ・プレイリスト</label>
      </div>
      <div id="bgm-frame"><div id="bgm-player"></div><div id="bgm-empty">好きな lo-fi の YouTube URL を<br>下に貼って Enter</div></div>
      <div id="bgm-win">
        <div id="bgm-win-status">別ウィンドウで再生します（Edge のログインがそのまま使えます）。<br>最小化しても流れ続けます。</div>
        <button id="bgm-win-open" class="ghost">ウィンドウを開く</button>
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
    setBgmOpen(store.get("bgm.open", true));
    $("#bgm-toggle").onclick = () => setBgmOpen($("#bgm").classList.contains("closed"));
    $("#bgm-focus").checked = bgm.focusOnly;
    document.querySelectorAll('[name="bgm-mode"]').forEach((r) => {
      r.checked = r.value === bgm.mode;
      r.onchange = () => setMode(r.value);
    });
    $("#bgm-win-open").onclick = () => openWindow();
    setMode(bgm.mode);
    $("#bgm-focus").onchange = (e) => { bgm.focusOnly = e.target.checked; store.set("bgm.focusOnly", bgm.focusOnly); };
    $("#bgm-play").onclick = () => isPlaying() ? pauseBgm() : playBgm();
    $("#bgm-select").onchange = (e) => {
      if (!e.target.value) return;
      setUrl(e.target.value);
      if (bgm.mode !== "embed") openWindow(); else playBgm();
    };
    $("#bgm-url").onkeydown = (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      if (!parseYouTube(e.target.value)) return say("その URL は YouTube の動画として読めなかったよ。");
      setUrl(e.target.value); e.target.value = "";
      if (bgm.mode !== "embed") openWindow(); else playBgm();
    };
  }

  function setMode(mode) {
    if (mode !== bgm.mode && bgm.mode === "embed") pauseEmbed();
    bgm.mode = mode; store.set("bgm.mode", mode);
    $("#bgm").classList.toggle("winmode", mode !== "embed");
    if (mode !== "embed") refreshWin();
  }

  async function media(action) {
    try {
      const r = await post("/api/media", { action });
      const s = r.sessions || [];
      bgm.winStatus = s.some((x) => x.status === "Playing") ? "Playing" : s.length ? "Paused" : "";
      const title = s[0]?.title;
      $("#bgm-win-status").innerHTML = s.length
        ? `${bgm.winStatus === "Playing" ? "▶ 再生中" : "⏸ 一時停止中"}<br><small>${esc(title || "YouTube")}</small>`
        : `${bgm.mode === "music" ? "YouTube Music" : "YouTube"} のウィンドウは開いていません。`;
      $("#bgm-play").textContent = bgm.winStatus === "Playing" ? "⏸" : "▶";
      // remember a URL the first time we learn its title
      if (title && bgm.mode === "window" && bgm.url && !bgm.presets.some((p) => p.url === bgm.url)) {
        post("/api/bgm", { url: bgm.url, title }).then((x) => { bgm.presets = x.bgm; renderPresets(); }).catch(() => {});
      }
      return s;
    } catch (e) { return []; }
  }
  const refreshWin = () => media("status");

  function toMusic(url) {
    // Same track or playlist on YouTube Music; no URL opens the user's YouTube Music home.
    const v = url ? parseYouTube(url) : null;
    if (!v || v.channel) return "https://music.youtube.com/";
    if (v.list) return `https://music.youtube.com/playlist?list=${encodeURIComponent(v.list)}`;
    return `https://music.youtube.com/watch?v=${encodeURIComponent(v.id)}`;
  }

  async function openWindow() {
    if (bgm.mode === "music") {
      await post("/api/bgm/window", { url: toMusic(bgm.url) });
      bgm.winOpen = true;
      say("YouTube Music を開いたよ。好きなプレイリストを流してね。", { ms: 3500 });
      return setTimeout(refreshWin, 6000);
    }
    if (!bgm.url) {
      const first = bgm.presets[0];
      if (!first) { $("#bgm-url").focus(); return say("流したい YouTube の URL を、BGM の欄に貼ってね。"); }
      setUrl(first.url);
    }
    await post("/api/bgm/window", { url: bgm.url });
    bgm.winOpen = true;
    say("YouTube のウィンドウを開いたよ。最小化しても流れるからね。", { ms: 3500 });
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

  function setUrl(url) {
    bgm.url = url; store.set("bgm.url", url); renderPresets();
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
        playerVars: { playsinline: 1, rel: 0, ...(v.list ? { listType: "playlist", list: v.list } : {}),
                      ...(v.channel ? { channel: v.channel } : {}) },
        events: {
          onReady: () => { bgm.ready = true; bgm.player.setVolume(Math.round(store.get("volume", 0.4) * 100)); bgm.pending?.(); bgm.pending = null; },
          onStateChange: () => {
            $("#bgm-play").textContent = isPlaying() ? "⏸" : "▶";
            // Remember a pasted URL once it really plays, with its title.
            if (isPlaying() && bgm.url && !bgm.presets.some((p) => p.url === bgm.url)) {
              const title = bgm.player.getVideoData?.().title || "YouTube";
              post("/api/bgm", { url: bgm.url, title }).then((r) => { bgm.presets = r.bgm; renderPresets(); }).catch(() => {});
            }
          },
          onError: (e) => {
            const url = bgm.url;
            say(e.data === 101 || e.data === 150 || e.data === 153
              ? "この動画はここでは再生できないみたい（埋め込みが許可されてない）。" : "BGM の再生でエラーが出ちゃった。",
              { buttons: [{ label: "YouTube ウィンドウで流す", onClick: () => {
                  document.querySelector('[name="bgm-mode"][value="window"]').checked = true;
                  setMode("window"); openWindow(); } }, { label: "閉じる" }] });
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
    if (!bgm.url) {
      const first = bgm.presets[0];
      if (!first) { $("#bgm-url").focus(); return say("好きな lo-fi の YouTube URL を、BGM の欄に貼ってね。"); }
      setUrl(first.url);
    }
    loadApi(() => {
      bgm.player.playVideo();
      if (!bgm.greeted) { bgm.greeted = true; say(pick(["BGM かけるね。", "いい感じの曲、流しとくね。"]), { ms: 2500 }); }
    });
  }
  function pauseEmbed() { if (bgm.ready) bgm.player.pauseVideo(); }

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
      document.querySelectorAll('[name="bgm-mode"]').forEach((r) => r.addEventListener("change", syncBgm));
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
      if (ev.some((e) => e.startsWith("✔"))) say(pick(["やったね！", "えらい！その調子。", "ひとつ片づいたね。"]), { ms: 3000 });
      else if (ev.some((e) => e.startsWith("→"))) say("Claude に頼んでおくね。右のボタンから開けるよ。", { ms: 3500 });
    },
    say,
  };
})();
