// The calendar pane (#157): the user's chosen Google calendars, a month at a glance, the chosen day's
// events and a form to add one. Event text comes from Google: it is only ever shown as text.
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const WEEK = "日月火水木金土";
  let month = new Date(); month.setDate(1);
  let day = iso(new Date());
  let data = { ready: false, calendars: [], events: [], sync: {} };
  let open = (() => { try { return localStorage.getItem("es.cal.open") !== "0"; } catch { return true; } })();

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-EsAgent": "1" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    return d;
  }

  function gridStart() { const d = new Date(month); d.setDate(1 - d.getDay()); return d; }

  // Which kept events fall on a day (all-day ends are exclusive, as Google stores them).
  function onDay(dayIso) {
    return data.events.filter((e) => {
      const s = e.start.slice(0, 10), end = e.end.slice(0, 10);
      return e.all_day ? s <= dayIso && dayIso < end : s <= dayIso && dayIso <= end;
    });
  }

  async function load() {
    const first = gridStart(), last = new Date(first); last.setDate(first.getDate() + 41);
    try { data = await api(`/api/calendar?from=${iso(first)}&to=${iso(last)}`); }
    catch (e) { data = { ...data, error: e.message }; }
    render();
  }

  function render() {
    const root = $("#cal");
    if (!root) return;
    root.classList.toggle("closed", !open);
    const head = `<div class="cal-head"><h2 id="cal-toggle"><span class="caret">▾</span> カレンダー</h2>` +
      (data.ready ? `<span class="cal-nav"><button type="button" class="ghost" data-move="-1" title="前の月">‹</button>` +
        `<b>${month.getFullYear()}年${month.getMonth() + 1}月</b>` +
        `<button type="button" class="ghost" data-move="1" title="次の月">›</button>` +
        `<button type="button" class="ghost" id="cal-sync" title="Google カレンダーから読み直す">⟳</button></span>` : "") + `</div>`;
    if (!data.ready) {
      root.innerHTML = head + `<div id="cal-body"><p class="cal-note">Google カレンダーの予定を読み取って、ここと会話で使えるようにします（予定の追加もできます）。` +
        `予定の中身はこの PC の中だけで使います。</p><button type="button" id="cal-connect">Google カレンダーに接続</button>` +
        `<p class="cal-note">ブラウザで許可したら、⟳ かこの画面の再読み込みで表示されます。</p></div>`;
      bind(); return;
    }
    const cells = [], start = gridStart(), today = iso(new Date());
    for (let i = 0; i < 42; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      const k = iso(d), n = onDay(k).length;
      cells.push(`<button type="button" class="cal-day${d.getMonth() !== month.getMonth() ? " other" : ""}${k === today ? " today" : ""}` +
        `${k === day ? " picked" : ""}" data-day="${k}" title="${n ? `${n} 件の予定` : ""}">${d.getDate()}${n ? `<i>${n > 3 ? "•••" : "•".repeat(n)}</i>` : ""}</button>`);
    }
    const list = onDay(day).map((e) => `<li><span class="when">${e.all_day ? "終日" : esc(e.start.slice(11, 16))}</span>` +
      `<span class="what">${esc(e.title)}</span><small>${esc(e.cal_name)}</small></li>`).join("") || `<li class="none">予定はありません</li>`;
    const writable = data.calendars.filter((c) => c.writable && c.selected);
    const choose = data.calendars.map((c) => `<label><input type="checkbox" data-cal="${esc(c.id)}"${c.selected ? " checked" : ""}>` +
      `<span class="swatch" style="background:${/^#[0-9a-fA-F]{3,8}$/.test(c.color) ? c.color : "#888"}"></span>${esc(c.name)}</label>`).join("");
    const [y, m, dd] = day.split("-");
    root.innerHTML = head + `<div id="cal-body">` +
      `<div class="cal-grid">${[...WEEK].map((w) => `<span class="cal-w">${w}</span>`).join("")}${cells.join("")}</div>` +
      `<h3>${+m}月${+dd}日（${WEEK[new Date(+y, +m - 1, +dd).getDay()]}）</h3><ul class="cal-list">${list}</ul>` +
      (writable.length ? `<form id="cal-add"><input name="title" placeholder="予定を追加" required maxlength="200">` +
        `<input name="date" type="date" value="${day}" required><input name="start" type="time" title="空なら終日">` +
        `<input name="end" type="time" title="空なら 1 時間">` +
        `<select name="calendar">${writable.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select>` +
        `<button type="submit">追加</button></form>` : "") +
      `<details class="cal-choose"><summary>表示するカレンダー</summary>${choose}</details>` +
      (data.sync?.error ? `<p class="cal-note error">${esc(data.sync.error)}</p>` : data.sync?.at ? `<p class="cal-note">読み込み ${esc(data.sync.at.slice(5, 16).replace("T", " "))}</p>` : "") +
      (data.error ? `<p class="cal-note error">${esc(data.error)}</p>` : "") + `</div>`;
    bind();
  }

  function bind() {
    $("#cal-toggle").onclick = () => { open = !open; try { localStorage.setItem("es.cal.open", open ? "1" : "0"); } catch {} render(); };
    $("#cal-connect") && ($("#cal-connect").onclick = async () => {
      try { await api("/api/calendar/connect", {}); window.Es?.say("ブラウザで Google カレンダーへのアクセスを許可してね。", { ms: 6000 }); }
      catch (e) { window.Es?.say(e.message, { ms: 6000 }); }
    });
    $("#cal-sync") && ($("#cal-sync").onclick = async () => {
      try { await api("/api/calendar/sync", {}); } catch (e) { window.Es?.say(e.message, { ms: 6000 }); }
      load();
    });
    document.querySelectorAll("#cal [data-move]").forEach((b) => b.onclick = () => { month.setMonth(month.getMonth() + +b.dataset.move); load(); });
    document.querySelectorAll("#cal [data-day]").forEach((b) => b.onclick = () => {
      day = b.dataset.day;
      const d = new Date(day + "T00:00");
      if (d.getMonth() !== month.getMonth()) { month = new Date(d.getFullYear(), d.getMonth(), 1); load(); } else render();
    });
    document.querySelectorAll("#cal [data-cal]").forEach((c) => c.onchange = async () => {
      const ids = [...document.querySelectorAll("#cal [data-cal]")].filter((x) => x.checked).map((x) => x.dataset.cal);
      try { await api("/api/calendar/select", { ids }); } catch (e) { window.Es?.say(e.message, { ms: 6000 }); }
      setTimeout(load, 2500);  // the server reads the newly chosen calendars in the background
    });
    $("#cal-add") && ($("#cal-add").onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try {
        const r = await api("/api/calendar/add", Object.fromEntries(f));
        window.Es?.say(`「${r.title}」を${r.calendar}に追加したよ。`, { ms: 4000 });
        day = f.get("date"); e.target.reset(); load();
      } catch (err) { window.Es?.say(err.message, { ms: 6000 }); }
    });
  }

  window.EsCalendar = { init() { render(); load(); setInterval(load, 5 * 60 * 1000); }, reload: load };
})();
