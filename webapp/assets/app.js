/* TaskPinger Mini App — day schedule, habits, tasks, stats. Vanilla JS, no build step. */
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const $ = (sel, root = document) => root.querySelector(sel);

  // ---------------------------------------------------------------- constants
  const WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"];
  const WDF = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"];
  const MON_G = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"];
  const MON_S = ["січ", "лют", "бер", "квіт", "трав", "черв", "лип", "серп", "вер", "жовт", "лист", "груд"];
  const COLORS = ["#ff7a1a", "#3d8bfd", "#2fbf71", "#9b6cff", "#ff5c8a", "#f5b83d", "#8a8f99"];
  const BLOCK_EMOJI = ["⏰", "🍳", "💼", "💻", "🏃", "🍽", "📚", "🧘", "🚿", "🛒", "🎮", "😴"];
  const HABIT_IDEAS = [["💧", "Вода 2 л"], ["🏃", "Зарядка"], ["📖", "Читання 20 хв"], ["🧘", "Медитація"], ["😴", "Сон до 23:30"], ["🚶", "10 000 кроків"]];
  const PRIO = { high: "Високий", medium: "Середній", low: "Низький" };
  const CAT = { personal: "Особисті", team: "Командні" };
  const REC = { none: "Одноразове", daily: "Щодня", weekly: "Щотижня", monthly: "Щомісяця" };

  // ---------------------------------------------------------------- state
  const S = {
    tab: "day",
    today: null,       // 'YYYY-MM-DD' (Kyiv, from server)
    date: null,        // date shown on the Day tab
    nowIso: null,      // server time at last fetch
    fetchedAt: 0,      // Date.now() at last fetch
    day: null,
    tasks: [],
    taskStatus: "pending",
    taskCat: "all",
    habits: [],
    statsDays: 7,
    stats: null,
    schedule: null,
    tplDay: 0,
    form: {},
    sheetStack: [],
  };

  // ---------------------------------------------------------------- utils
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");
  const addDays = (iso, n) => { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const weekdayOf = (iso) => (new Date(iso + "T00:00:00Z").getUTCDay() + 6) % 7;
  const fmtDate = (iso) => `${+iso.slice(8, 10)} ${MON_G[+iso.slice(5, 7) - 1]}`;
  const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
  const fromMin = (m) => `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`;

  function nowMin() {
    if (!S.nowIso) return 0;
    const base = toMin(S.nowIso.slice(11, 16));
    return Math.min(1439, base + Math.floor((Date.now() - S.fetchedAt) / 60000));
  }

  function relDay(iso) {
    if (iso === S.today) return "сьогодні";
    if (iso === addDays(S.today, 1)) return "завтра";
    if (iso === addDays(S.today, -1)) return "вчора";
    const wd = weekdayOf(iso);
    const diff = (new Date(iso) - new Date(S.today)) / 864e5;
    if (diff > 0 && diff < 7) return WD[wd];
    return `${+iso.slice(8, 10)} ${MON_S[+iso.slice(5, 7) - 1]}`;
  }

  const dlLabel = (dl) => `${relDay(dl.slice(0, 10))} ${dl.slice(11, 16)}`;

  function durLabel(min) {
    if (min < 60) return `${min} хв`;
    const h = Math.floor(min / 60), m = min % 60;
    return m ? `${h} год ${m} хв` : `${h} год`;
  }

  function daysLabel(days) {
    if (days === "0123456") return "щодня";
    if (days === "01234") return "будні";
    if (days === "56") return "вихідні";
    return days.split("").map((d) => WD[+d]).join(" ");
  }

  const haptic = (kind = "light") => {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "success" || kind === "error" || kind === "warning") tg.HapticFeedback.notificationOccurred(kind);
      else tg.HapticFeedback.impactOccurred(kind);
    } catch (_) { /* old client */ }
  };

  function confirmBox(msg) {
    return new Promise((resolve) => {
      if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast("6.2")) tg.showConfirm(msg, (ok) => resolve(!!ok));
      else resolve(window.confirm(msg));
    });
  }

  let toastTimer = null;
  function toast(text, undo) {
    const el = $("#toast");
    el.innerHTML = `<span>${esc(text)}</span>` + (undo ? `<button class="undo" data-act="toast-undo">Скасувати</button>` : "");
    el.hidden = false;
    S.toastUndo = undo || null;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; S.toastUndo = null; }, undo ? 5000 : 2500);
  }

  // ---------------------------------------------------------------- api
  async function api(method, path, body) {
    const r = await fetch("api/" + path, {
      method,
      headers: { "Content-Type": "application/json", "X-Init-Data": (tg && tg.initData) || "" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const j = await r.json().catch(() => ({}));
    if (r.status === 401) throw Object.assign(new Error("Сесія застаріла — перевідкрий апку"), { fatal: true });
    if (!r.ok) throw new Error(j.error || `Помилка ${r.status}`);
    return j;
  }

  async function run(fn) {
    try { return await fn(); }
    catch (e) { haptic("error"); toast(e.message || "Помилка"); throw e; }
  }

  // ---------------------------------------------------------------- loaders
  async function loadDay() {
    const d = await api("GET", "day?date=" + (S.date || ""));
    S.day = d; S.nowIso = d.now; S.fetchedAt = Date.now();
    if (!S.today) S.today = d.now.slice(0, 10);
    if (!S.date) S.date = d.date;
  }
  async function loadTasks() { S.tasks = (await api("GET", "tasks?status=" + S.taskStatus)).tasks; }
  async function loadHabits() { S.habits = (await api("GET", "habits")).habits; }
  async function loadStats() { S.stats = await api("GET", "stats?days=" + S.statsDays); }
  async function loadSchedule() { S.schedule = (await api("GET", "schedule")).days; }

  async function refresh() {
    if (S.tab === "day") await loadDay();
    else if (S.tab === "tasks") await loadTasks();
    else if (S.tab === "habits") await loadHabits();
    else if (S.tab === "stats") await loadStats();
    render();
  }

  // ---------------------------------------------------------------- render: shell
  function render() {
    document.querySelectorAll("#tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.tab === S.tab));
    const view = $("#view");
    if (S.tab === "day") view.innerHTML = renderDay();
    else if (S.tab === "tasks") view.innerHTML = renderTasks();
    else if (S.tab === "habits") view.innerHTML = renderHabits();
    else if (S.tab === "stats") view.innerHTML = renderStats();
    renderClock();
  }

  function renderClock() {
    if (!S.nowIso) return;
    const wd = weekdayOf(S.today);
    $("#clock").textContent = `${WD[wd]}, ${fmtDate(S.today)} · ${fromMin(nowMin())}`;
  }

  // ---------------------------------------------------------------- render: day
  function blockSpan(b) {
    const s = toMin(b.start);
    let e = b.end === "24:00" ? 1440 : toMin(b.end);
    if (e <= s) e += 1440; // crosses midnight
    return [s, e];
  }

  function renderDay() {
    const d = S.day;
    if (!d) return `<div class="skeleton"></div><div class="skeleton"></div>`;
    const isToday = d.is_today;
    const now = nowMin();
    const active = d.blocks.filter((b) => !b.skipped);
    const current = isToday ? active.find((b) => { const [s, e] = blockSpan(b); return s <= now && now < e; }) : null;
    const next = isToday ? active.find((b) => blockSpan(b)[0] > now) : null;
    const habitsToday = d.habits.filter((h) => h.scheduled);
    const tasksDue = d.tasks.due.length + d.tasks.done.length;

    let out = `
      <div class="daynav">
        <button class="arrow" data-act="day-shift" data-n="-1" aria-label="Попередній день">‹</button>
        <div class="title"><b>${esc(d.weekday_name)}</b><span>${fmtDate(d.date)}${isToday ? " · сьогодні" : ""}</span></div>
        <button class="arrow" data-act="day-shift" data-n="1" aria-label="Наступний день">›</button>
      </div>
      ${isToday ? "" : `<div style="text-align:center;margin-top:8px"><button class="today-link" data-act="day-today">↩ До сьогодні</button></div>`}`;

    // --- now card
    const doneBlocks = active.filter((b) => b.done).length;
    const doneHabits = habitsToday.filter((h) => h.done).length;
    let head = "";
    if (isToday) {
      if (current) {
        const [s, e] = blockSpan(current);
        const pct = Math.round(((now - s) / (e - s)) * 100);
        head = `<div class="lbl">Зараз</div>
          <div class="big">${esc(current.emoji)} ${esc(current.title)}</div>
          <div class="muted small">${current.start}–${current.end} · лишилось ${durLabel(e - now)}</div>
          <div class="progress"><i style="width:${pct}%"></i></div>`;
      } else if (next) {
        head = `<div class="lbl">Вільний час</div>
          <div class="big">до ${next.start}</div>
          <div class="muted small">через ${durLabel(blockSpan(next)[0] - now)}</div>`;
      } else {
        head = `<div class="lbl">${active.length ? "Розклад на сьогодні завершено" : "Розклад не задано"}</div>
          <div class="big">${active.length ? "Гарного вечора 🌙" : "Вільний день"}</div>`;
      }
      if (next && current) head += `<div class="next">Далі о <b>${next.start}</b> — ${esc(next.emoji)} ${esc(next.title)}</div>`;
    } else {
      head = `<div class="lbl">${d.date < S.today ? "Минулий день" : "План"}</div>
        <div class="big">${active.length} блоків у розкладі</div>`;
    }
    out += `<div class="card now-card">${head}
      <div class="kpis">
        <div class="kpi"><b>${doneBlocks}/${active.length}</b><span>розклад</span></div>
        <div class="kpi"><b>${doneHabits}/${habitsToday.length}</b><span>звички</span></div>
        <div class="kpi"><b>${d.tasks.done.length}/${tasksDue}</b><span>завдання</span></div>
      </div></div>`;

    // --- timeline
    out += `<div class="section-h"><span>Розклад</span><button class="act" data-act="tpl-open">⚙ Шаблон тижня</button></div>`;
    if (!d.blocks.length) {
      out += `<div class="card empty">На ${WDF[d.weekday].toLowerCase()} розклад порожній.<br><br>
        <button class="btn primary sm" data-act="tpl-open">Налаштувати шаблон тижня</button></div>`;
    } else {
      out += `<div class="timeline">` + d.blocks.map((b) => {
        const [s, e] = blockSpan(b);
        const cls = ["blk"];
        if (b.skipped) cls.push("skipped");
        if (b.done) cls.push("done");
        if (isToday && !b.skipped && e <= now) cls.push("past");
        if (current && b.key === current.key) cls.push("current");
        const tag = b.skipped ? "пропущено сьогодні" : b.extra ? "тільки цей день" : b.edited ? "змінено на цей день" : durLabel(e - s);
        return `<div class="${cls.join(" ")}">
          <div class="time" data-act="block" data-key="${b.key}"><b>${b.start}</b>${b.end}</div>
          <div class="bar" style="background:${esc(b.color || "var(--muted)")}"></div>
          <div class="body" data-act="block" data-key="${b.key}">
            <div class="t ellipsis">${esc(b.emoji)} ${esc(b.title)}</div>
            <div class="meta">${tag}</div>
          </div>
          ${b.skipped ? "" : `<button class="check ${b.done ? "on" : ""}" data-act="block-toggle" data-key="${b.key}" aria-label="Виконано"></button>`}
        </div>`;
      }).join("") + `</div>`;
    }
    out += `<button class="btn block sm" style="margin-top:6px" data-act="extra-new">＋ Блок тільки на цей день</button>`;

    // --- habits
    out += `<div class="section-h"><span>Звички</span><button class="act" data-act="tab" data-tab="habits">Усі →</button></div>`;
    if (!d.habits.length) {
      out += `<div class="card empty">Ще немає звичок. <button class="today-link" data-act="habit-new">Додати</button></div>`;
    } else if (!habitsToday.length) {
      out += `<div class="card empty">На цей день звичок не заплановано</div>`;
    } else {
      out += `<div class="habit-grid">` + habitsToday.map((h) => `
        <button class="hchip ${h.done ? "on" : ""}" data-act="habit-toggle" data-id="${h.id}" data-date="${d.date}">
          <span class="e">${esc(h.emoji || "•")}</span>
          <span class="grow"><span class="n">${esc(h.title)}</span><br><span class="s">${h.streak ? "🔥 " + h.streak : "почни серію"}</span></span>
        </button>`).join("") + `</div>`;
    }

    // --- tasks
    out += `<div class="section-h"><span>Завдання</span><button class="act" data-act="task-new" data-date="${d.date}">＋ Додати</button></div>`;
    const rows = [...d.tasks.overdue, ...d.tasks.due];
    if (!rows.length && !d.tasks.done.length) out += `<div class="card empty">Дедлайнів на цей день немає</div>`;
    out += rows.map(taskRow).join("") + d.tasks.done.map(taskRow).join("");
    return out;
  }

  // ---------------------------------------------------------------- render: tasks
  function taskRow(t) {
    const done = t.status === "done";
    const meta = [];
    if (t.deadline) {
      const cls = done ? "" : t.deadline_state === "overdue" ? "overdue" : t.deadline_state === "today" ? "today" : "";
      meta.push(`<span class="${cls}">⏰ ${dlLabel(t.deadline)}</span>`);
    }
    if (t.recurrence && t.recurrence !== "none") meta.push(`<span>🔁 ${REC[t.recurrence]}</span>`);
    if (t.category === "team") meta.push(`<span>👥</span>`);
    if (done && t.completed_at) meta.push(`<span>✓ ${dlLabel(t.completed_at)}</span>`);
    return `<div class="task ${done ? "done" : ""}">
      <span class="prio ${esc(t.priority)}"></span>
      <div class="grow" data-act="task-open" data-id="${t.id}">
        <div class="t">${esc(t.title)}</div>
        ${meta.length ? `<div class="meta">${meta.join("")}</div>` : ""}
      </div>
      <button class="check ${done ? "on" : ""}" data-act="${done ? "task-undo" : "task-done"}" data-id="${t.id}" aria-label="Виконано"></button>
    </div>`;
  }

  function groupTasks(tasks) {
    const g = { overdue: [], today: [], tomorrow: [], week: [], later: [], none: [] };
    const tmr = addDays(S.today, 1), wk = addDays(S.today, 7);
    for (const t of tasks) {
      if (!t.deadline) { g.none.push(t); continue; }
      const d = t.deadline.slice(0, 10);
      if (t.deadline_state === "overdue") g.overdue.push(t);
      else if (d === S.today) g.today.push(t);
      else if (d === tmr) g.tomorrow.push(t);
      else if (d <= wk) g.week.push(t);
      else g.later.push(t);
    }
    return [["🔴 Прострочені", g.overdue], ["Сьогодні", g.today], ["Завтра", g.tomorrow], ["Цього тижня", g.week], ["Пізніше", g.later], ["Без дедлайну", g.none]];
  }

  function renderTasks() {
    let out = `
      <form class="quickadd" data-form="quickadd" autocomplete="off">
        <input id="qa" placeholder="Нове завдання…" enterkeyhint="done" maxlength="300">
        <button class="btn primary" type="submit" aria-label="Додати">＋</button>
      </form>
      <div class="seg" style="margin-top:12px">
        <button class="${S.taskStatus === "pending" ? "on" : ""}" data-act="task-status" data-v="pending">Активні</button>
        <button class="${S.taskStatus === "done" ? "on" : ""}" data-act="task-status" data-v="done">Виконані</button>
      </div>
      <div class="chips" style="margin-top:10px">
        ${[["all", "Усі"], ["personal", "👤 Особисті"], ["team", "👥 Командні"]].map(([k, l]) =>
          `<button class="chip ${S.taskCat === k ? "on" : ""}" data-act="task-cat" data-v="${k}">${l}</button>`).join("")}
      </div>`;
    const list = S.tasks.filter((t) => S.taskCat === "all" || t.category === S.taskCat);
    if (!list.length) return out + `<div class="card empty">${S.taskStatus === "done" ? "Ще нічого не виконано" : "Порожньо — красота 🎉"}</div>`;
    if (S.taskStatus === "done") return out + `<div style="margin-top:10px">${list.map(taskRow).join("")}</div>`;
    for (const [title, items] of groupTasks(list)) {
      if (!items.length) continue;
      out += `<div class="section-h"><span>${title} · ${items.length}</span></div>` + items.map(taskRow).join("");
    }
    return out;
  }

  // ---------------------------------------------------------------- render: habits
  function renderHabits() {
    let out = `<div class="section-h" style="margin-top:8px"><span>Звички</span><button class="act" data-act="habit-new">＋ Нова</button></div>`;
    if (!S.habits.length) {
      return out + `<div class="card"><div class="muted small" style="margin-bottom:10px">Додай щоденні звички — я рахуватиму серії 🔥 і покажу їх у ранковому дайджесті.</div>
        <div class="chips" style="flex-wrap:wrap">${HABIT_IDEAS.map(([e, t]) =>
          `<button class="chip" data-act="habit-quick" data-e="${e}" data-t="${esc(t)}">${e} ${esc(t)}</button>`).join("")}</div></div>`;
    }
    const days = Array.from({ length: 14 }, (_, i) => addDays(S.today, i - 13));
    out += S.habits.map((h) => {
      const log = new Set(h.log);
      const dots = days.map((d) => {
        const cls = [log.has(d) ? "on" : "", h.days.includes(String(weekdayOf(d))) ? "" : "off", d === S.today ? "today" : ""].join(" ");
        return `<i class="${cls}" data-act="habit-toggle" data-id="${h.id}" data-date="${d}" title="${fmtDate(d)}"></i>`;
      }).join("");
      const doneToday = log.has(S.today);
      return `<div class="hrow">
        <div class="e">${esc(h.emoji || "•")}</div>
        <div class="grow" data-act="habit-open" data-id="${h.id}">
          <div class="row"><b class="grow ellipsis">${esc(h.title)}</b><span class="streak">${h.streak ? "🔥 " + h.streak : ""}</span></div>
          <div class="muted small">${daysLabel(h.days)}${h.best ? " · рекорд " + h.best : ""}</div>
          <div class="dots">${dots}</div>
        </div>
        <button class="check ${doneToday ? "on" : ""}" data-act="habit-toggle" data-id="${h.id}" data-date="${S.today}" aria-label="Сьогодні виконано"></button>
      </div>`;
    }).join("");
    return out + `<div class="muted small" style="text-align:center;margin-top:8px">Тисни квадратик, щоб відмітити минулий день</div>`;
  }

  // ---------------------------------------------------------------- render: stats
  function pctOf(arr) {
    const done = arr.reduce((a, x) => a + x.done, 0), total = arr.reduce((a, x) => a + x.total, 0);
    return total ? Math.round((done / total) * 100) : null;
  }

  function roundedTop(x, y, w, h, r) {
    r = Math.min(r, w / 2, h);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  function barChart(series, opt) {
    // series: [{date, value|null, tip}]
    const W = 340, H = 130, padL = 26, padB = 18, padT = 14;
    const n = series.length, plotW = W - padL, plotH = H - padB - padT;
    const max = opt.max || Math.max(1, ...series.map((s) => s.value || 0));
    const gap = n > 14 ? 2 : 6;
    const bw = (plotW - gap * (n - 1)) / n;
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opt.title)}">`;
    svg += `<line class="grid" x1="${padL}" x2="${W}" y1="${padT}" y2="${padT}" stroke-dasharray="3 4"/>`;
    svg += `<text class="axis" x="${padL - 6}" y="${padT + 3}" text-anchor="end">${opt.max ? max + "%" : max}</text>`;
    svg += `<line class="grid" x1="${padL}" x2="${W}" y1="${H - padB}" y2="${H - padB}"/>`;
    svg += `<text class="axis" x="${padL - 6}" y="${H - padB + 3}" text-anchor="end">0</text>`;
    const maxIdx = series.reduce((bi, s, i) => ((s.value || 0) > (series[bi].value || 0) ? i : bi), 0);
    series.forEach((s, i) => {
      const x = padL + i * (bw + gap);
      const v = s.value;
      const h = v ? Math.max(3, (v / max) * plotH) : 2;
      const y = H - padB - h;
      svg += `<rect class="hit" x="${x - gap / 2}" y="${padT}" width="${bw + gap}" height="${plotH + padB}" data-tip="${esc(s.tip)}"/>`;
      svg += v ? `<path class="bar" d="${roundedTop(x, y, bw, h, 4)}"/>` : `<rect class="bar zero" x="${x}" y="${H - padB - 2}" width="${bw}" height="2"/>`;
      if (v && i === maxIdx && n <= 14) svg += `<text class="vlabel" x="${x + bw / 2}" y="${y - 4}" text-anchor="middle">${opt.max ? v + "%" : v}</text>`;
      const showX = n <= 7 || i % 5 === 0 || i === n - 1;
      if (showX) {
        const lbl = n <= 7 ? WD[weekdayOf(s.date)] : String(+s.date.slice(8, 10));
        svg += `<text class="axis" x="${x + bw / 2}" y="${H - 4}" text-anchor="middle">${lbl}</text>`;
      }
    });
    return svg + `</svg>`;
  }

  function renderStats() {
    const st = S.stats;
    let out = `<div class="seg" style="margin-top:8px">
      <button class="${S.statsDays === 7 ? "on" : ""}" data-act="stats-days" data-v="7">7 днів</button>
      <button class="${S.statsDays === 30 ? "on" : ""}" data-act="stats-days" data-v="30">30 днів</button>
    </div>`;
    if (!st) return out + `<div class="skeleton"></div>`;
    const hp = pctOf(st.habits), sp = pctOf(st.schedule);
    out += `<div class="tiles">
      <div class="tile"><div class="v">${st.totals.done}</div><div class="k">завдань виконано</div></div>
      <div class="tile"><div class="v">${st.totals.pending}</div><div class="k">в роботі${st.totals.overdue ? ` · <span style="color:var(--bad)">${st.totals.overdue} прострочено</span>` : ""}</div></div>
      <div class="tile"><div class="v">${hp == null ? "—" : hp + "%"}</div><div class="k">звичок виконано</div></div>
      <div class="tile"><div class="v">${sp == null ? "—" : sp + "%"}</div><div class="k">дотримання розкладу</div></div>
    </div>`;

    const dayTip = (d) => `${WD[weekdayOf(d)]} ${d.slice(8, 10)}.${d.slice(5, 7)}`;
    const tasksS = st.tasks.map((x) => ({ date: x.date, value: x.done, tip: `${dayTip(x.date)} · ${x.done} викон.` }));
    const pctS = (arr) => arr.map((x) => ({
      date: x.date,
      value: x.total ? Math.round((x.done / x.total) * 100) : null,
      tip: x.total ? `${dayTip(x.date)} · ${x.done}/${x.total}` : `${dayTip(x.date)} · нічого не заплановано`,
    }));
    const chart = (title, sub, svg) => `<div class="card chart-card"><h3>${title}</h3><div class="sub">${sub}</div><div class="chart">${svg}</div></div>`;
    out += chart("Виконані завдання", "кількість за день", barChart(tasksS, { title: "Виконані завдання" }));
    out += chart("Звички", "% виконаних із запланованих", barChart(pctS(st.habits), { title: "Звички", max: 100 }));
    out += chart("Розклад", "% блоків, позначених виконаними", barChart(pctS(st.schedule), { title: "Розклад", max: 100 }));

    if (st.habit_rows.length) {
      out += `<div class="section-h"><span>Серії звичок</span></div><div class="card">` + st.habit_rows.map((h) => {
        const p = h.total ? Math.round((h.hit / h.total) * 100) : 0;
        return `<div style="margin:6px 0 12px">
          <div class="row"><span>${esc(h.emoji || "•")}</span><b class="grow ellipsis">${esc(h.title)}</b>
            <span class="streak">🔥 ${h.streak}</span><span class="muted small">рекорд ${h.best}</span></div>
          <div class="row small muted" style="margin-top:4px"><div class="progress grow" style="margin:0"><i style="width:${p}%"></i></div><span>${h.hit}/${h.total}</span></div>
        </div>`;
      }).join("") + `</div>`;
    }

    out += `<details class="card small"><summary class="muted">Таблиця по днях</summary>
      <table style="width:100%;margin-top:8px;border-collapse:collapse;font-variant-numeric:tabular-nums">
        <tr class="muted"><td>День</td><td align="right">Завдання</td><td align="right">Звички</td><td align="right">Розклад</td></tr>
        ${st.tasks.map((x, i) => `<tr><td>${dayTip(x.date)}</td><td align="right">${x.done}</td>
          <td align="right">${st.habits[i].total ? st.habits[i].done + "/" + st.habits[i].total : "—"}</td>
          <td align="right">${st.schedule[i].total ? st.schedule[i].done + "/" + st.schedule[i].total : "—"}</td></tr>`).reverse().join("")}
      </table></details>`;
    return out;
  }

  // ---------------------------------------------------------------- sheets
  function openSheet(fn) { S.sheetStack.push(fn); renderSheet(); }
  function replaceSheet(fn) { S.sheetStack[S.sheetStack.length - 1] = fn; renderSheet(); }
  function popSheet() { S.sheetStack.pop(); renderSheet(); }
  function closeSheets() { S.sheetStack = []; renderSheet(); }

  function renderSheet() {
    const sheet = $("#sheet"), bd = $("#sheet-backdrop");
    const top = S.sheetStack[S.sheetStack.length - 1];
    if (!top) {
      sheet.hidden = true; bd.hidden = true;
      if (tg && tg.BackButton) tg.BackButton.hide();
      return;
    }
    sheet.innerHTML = `<div class="grab"></div>` + top();
    sheet.hidden = false; bd.hidden = false;
    if (tg && tg.BackButton) tg.BackButton.show();
  }

  // --- block action menu (Day tab)
  function blockMenu(key) {
    return () => {
      const b = S.day && S.day.blocks.find((x) => x.key === key);
      if (!b) return `<div class="empty">Блок не знайдено</div>`;
      const items = [];
      if (!b.skipped) items.push(["block-toggle", b.done ? "↩️" : "✅", b.done ? "Зняти позначку" : "Позначити виконаним"]);
      if (!b.skipped) items.push(["ovr-edit", "✏️", b.extra ? "Редагувати" : "Змінити тільки на цей день"]);
      if (!b.extra && !b.skipped) items.push(["ovr-skip", "⏭", "Пропустити цей день"]);
      if (!b.extra && (b.edited || b.skipped)) items.push(["ovr-reset", "↩️", "Повернути як у шаблоні"]);
      if (b.block_id) items.push(["tpl-edit-from-day", "⚙", `Редагувати в шаблоні (${WD[S.day.weekday]})`]);
      if (b.extra) items.push(["ovr-delete", "🗑", "Видалити блок"]);
      return `<h2>${esc(b.emoji)} ${esc(b.title)}</h2>
        <div class="muted small" style="margin-top:-8px">${b.start}–${b.end} · ${fmtDate(S.day.date)}</div>
        <div class="menu-list" style="margin-top:8px">${items.map(([act, i, l]) =>
          `<button data-act="${act}" data-key="${b.key}"><span class="i">${i}</span>${l}</button>`).join("")}</div>`;
    };
  }

  // --- block form: modes tpl-new | tpl-edit | ovr-edit | extra-new | extra-edit
  function blockForm() {
    const f = S.form;
    const titles = { "tpl-new": "Новий блок у шаблоні", "tpl-edit": "Блок шаблону", "ovr-edit": "Зміна на " + fmtDate(f.date || S.today), "extra-new": "Блок на " + fmtDate(f.date || S.today), "extra-edit": "Блок на " + fmtDate(f.date || S.today) };
    return `<h2>${titles[f.mode]}</h2>
      <form data-form="block" autocomplete="off">
        <div class="field"><label>Назва</label>
          <div class="row"><input class="input emoji-in" data-f="emoji" value="${esc(f.emoji)}" maxlength="8" placeholder="🙂">
          <input class="input grow" data-f="title" value="${esc(f.title)}" maxlength="120" placeholder="Напр. Спорт" required></div>
          <div class="chips" style="margin-top:8px">${BLOCK_EMOJI.map((e) => `<button type="button" class="chip" data-act="form-set" data-k="emoji" data-v="${e}">${e}</button>`).join("")}</div>
        </div>
        <div class="field two">
          <div><label>Початок</label><input class="input" type="time" data-f="start" value="${esc(f.start)}" required></div>
          <div><label>Кінець</label><input class="input" type="time" data-f="end" value="${esc(f.end)}" required></div>
        </div>
        <div class="field"><label>Колір</label><div class="swatches">${COLORS.map((c) =>
          `<button type="button" class="sw ${f.color === c ? "on" : ""}" style="background:${c}" data-act="form-set" data-k="color" data-v="${c}" aria-label="колір"></button>`).join("")}</div></div>
        ${f.mode === "tpl-new" ? `<div class="field"><label>Дні тижня</label>${daySelect(f.days)}
          <div class="chips" style="margin-top:8px">
            <button type="button" class="chip" data-act="form-set" data-k="days" data-v="01234">Будні</button>
            <button type="button" class="chip" data-act="form-set" data-k="days" data-v="56">Вихідні</button>
            <button type="button" class="chip" data-act="form-set" data-k="days" data-v="0123456">Щодня</button>
          </div></div>` : ""}
        <div class="btns">
          ${f.mode === "tpl-edit" ? `<button type="button" class="btn danger" data-act="tpl-delete">🗑</button>` : ""}
          <button type="submit" class="btn primary">Зберегти</button>
        </div>
      </form>`;
  }

  function daySelect(days) {
    return `<div class="daysel">${WD.map((w, i) =>
      `<button type="button" class="${String(days || "").includes(String(i)) ? "on" : ""}" data-act="form-day" data-v="${i}">${w}</button>`).join("")}</div>`;
  }

  // --- weekly template editor
  function tplEditor() {
    const days = S.schedule || {};
    const list = days[String(S.tplDay)] || [];
    return `<h2>Шаблон тижня</h2>
      <div class="daysel">${WD.map((w, i) => `<button class="${S.tplDay === i ? "on" : ""}" data-act="tpl-day" data-v="${i}">${w}<br><span style="font-size:10px;opacity:.8">${(days[String(i)] || []).length}</span></button>`).join("")}</div>
      <div style="margin-top:10px">${list.length ? list.map((b) => `
        <div class="blk" data-act="tpl-edit" data-id="${b.id}">
          <div class="time"><b>${b.start}</b>${b.end}</div>
          <div class="bar" style="background:${esc(b.color || "var(--muted)")}"></div>
          <div class="body"><div class="t ellipsis">${esc(b.emoji)} ${esc(b.title)}</div></div>
          <span class="muted">›</span>
        </div>`).join("") : `<div class="empty">${WDF[S.tplDay]}: порожньо</div>`}</div>
      <div class="btns">
        <button class="btn" data-act="tpl-copy" ${list.length ? "" : "disabled"}>⧉ Копіювати на…</button>
        <button class="btn primary" data-act="tpl-new">＋ Блок</button>
      </div>
      <div class="muted small" style="margin-top:10px">Шаблон повторюється щотижня. Зміни «тільки на день» робляться з вкладки День.</div>`;
  }

  function tplCopySheet() {
    const f = S.form;
    return `<h2>Копіювати ${WDF[S.tplDay].toLowerCase()}</h2>
      <div class="muted small" style="margin:-6px 0 12px">Розклад обраних днів буде <b>замінено</b> копією.</div>
      ${daySelect(f.days)}
      <div class="chips" style="margin-top:8px">
        <button class="chip" data-act="form-set" data-k="days" data-v="01234">Будні</button>
        <button class="chip" data-act="form-set" data-k="days" data-v="56">Вихідні</button>
        <button class="chip" data-act="form-set" data-k="days" data-v="0123456">Усі</button>
      </div>
      <div class="btns"><button class="btn primary" data-act="tpl-copy-go">Копіювати</button></div>`;
  }

  // --- task form
  function taskForm() {
    const f = S.form;
    const tmr = addDays(S.today, 1);
    const quick = [
      ["Сьогодні 18:00", S.today + "T18:00"], ["Завтра 09:00", tmr + "T09:00"],
      ["Через тиждень", addDays(S.today, 7) + "T09:00"],
    ];
    return `<h2>${f.id ? "Завдання #" + f.id : "Нове завдання"}</h2>
      <form data-form="task" autocomplete="off">
        <div class="field"><label>Назва</label><input class="input" data-f="title" value="${esc(f.title)}" maxlength="300" required></div>
        <div class="field"><label>Дедлайн</label>
          <input class="input" type="datetime-local" data-f="deadline" value="${esc(f.deadline || "")}">
          <div class="chips" style="margin-top:8px">${quick.map(([l, v]) =>
            `<button type="button" class="chip ${f.deadline === v ? "on" : ""}" data-act="form-set" data-k="deadline" data-v="${v}">${l}</button>`).join("")}
            <button type="button" class="chip ${!f.deadline ? "on" : ""}" data-act="form-set" data-k="deadline" data-v="">Без дедлайну</button></div>
          ${f.deadline ? `<div class="muted small" style="margin-top:6px">🔔 Нагадаю за добу і за 2 год до дедлайну</div>` : ""}
        </div>
        <div class="field"><label>Пріоритет</label><div class="seg">${Object.entries(PRIO).map(([k, l]) =>
          `<button type="button" class="${f.priority === k ? "on" : ""}" data-act="form-set" data-k="priority" data-v="${k}">${l}</button>`).join("")}</div></div>
        <div class="field"><label>Категорія</label><div class="seg">${Object.entries(CAT).map(([k, l]) =>
          `<button type="button" class="${f.category === k ? "on" : ""}" data-act="form-set" data-k="category" data-v="${k}">${l}</button>`).join("")}</div></div>
        <div class="field"><label>Повторення</label>
          <select data-f="recurrence">${Object.entries(REC).map(([k, l]) => `<option value="${k}" ${f.recurrence === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="field"><label>Нотатка</label><textarea data-f="description" maxlength="2000">${esc(f.description)}</textarea></div>
        <div class="btns">
          ${f.id ? `<button type="button" class="btn danger" data-act="task-delete" data-id="${f.id}">🗑</button>` : ""}
          ${f.id && f.status !== "done" ? `<button type="button" class="btn" data-act="task-done" data-id="${f.id}">✅ Готово</button>` : ""}
          <button type="submit" class="btn primary">Зберегти</button>
        </div>
      </form>`;
  }

  // --- habit form
  function habitForm() {
    const f = S.form;
    return `<h2>${f.id ? "Звичка" : "Нова звичка"}</h2>
      <form data-form="habit" autocomplete="off">
        <div class="field"><label>Назва</label>
          <div class="row"><input class="input emoji-in" data-f="emoji" value="${esc(f.emoji)}" maxlength="8" placeholder="✨">
          <input class="input grow" data-f="title" value="${esc(f.title)}" maxlength="120" placeholder="Напр. Вода 2 л" required></div></div>
        <div class="field"><label>Дні</label>${daySelect(f.days)}
          <div class="chips" style="margin-top:8px">
            <button type="button" class="chip" data-act="form-set" data-k="days" data-v="0123456">Щодня</button>
            <button type="button" class="chip" data-act="form-set" data-k="days" data-v="01234">Будні</button>
            <button type="button" class="chip" data-act="form-set" data-k="days" data-v="56">Вихідні</button>
          </div></div>
        <div class="btns">
          ${f.id ? `<button type="button" class="btn danger" data-act="habit-delete" data-id="${f.id}">🗑</button>` : ""}
          <button type="submit" class="btn primary">Зберегти</button>
        </div>
      </form>`;
  }

  // ---------------------------------------------------------------- actions
  function findDayBlock(key) { return S.day && S.day.blocks.find((b) => b.key === key); }

  async function afterChange() {
    await refresh();
    if (S.sheetStack.length) renderSheet();
  }

  async function toggleTaskDone(id) {
    const r = await run(() => api("POST", `tasks/${id}/done`));
    haptic("success");
    const spawnedId = r.spawned ? r.spawned.id : null;
    toast(r.spawned ? `Готово ✓ Наступне: ${dlLabel(r.spawned.deadline)}` : "Готово ✓", async () => {
      await run(() => api("POST", `tasks/${id}/undo`, { spawned_id: spawnedId }));
      await afterChange();
    });
    await afterChange();
  }

  const actions = {
    tab: (el) => switchTab(el.dataset.tab),
    "toast-undo": async () => { const fn = S.toastUndo; $("#toast").hidden = true; S.toastUndo = null; if (fn) await fn(); },

    // day nav
    "day-shift": async (el) => { S.date = addDays(S.date, +el.dataset.n); haptic(); await run(loadDay); render(); },
    "day-today": async () => { S.date = S.today; await run(loadDay); render(); },

    // blocks
    block: (el) => { haptic(); openSheet(blockMenu(el.dataset.key)); },
    "block-toggle": async (el) => {
      const r = await run(() => api("POST", `day/${S.day.date}/toggle`, { key: el.dataset.key }));
      haptic(r.done ? "success" : "light");
      if (S.sheetStack.length) closeSheets();
      await afterChange();
    },
    "ovr-edit": (el) => {
      const b = findDayBlock(el.dataset.key);
      S.form = { mode: b.extra ? "extra-edit" : "ovr-edit", date: S.day.date, block_id: b.block_id, override_id: b.override_id,
        title: b.title, emoji: b.emoji, color: b.color, start: b.start, end: b.end === "24:00" ? "23:59" : b.end };
      replaceSheet(blockForm);
    },
    "ovr-skip": async (el) => {
      const b = findDayBlock(el.dataset.key);
      await run(() => api("POST", `day/${S.day.date}/override`, { block_id: b.block_id, skipped: true }));
      closeSheets(); toast("Пропущено на цей день"); await afterChange();
    },
    "ovr-reset": async (el) => {
      const b = findDayBlock(el.dataset.key);
      await run(() => api("DELETE", `override/${b.override_id}`));
      closeSheets(); await afterChange();
    },
    "ovr-delete": async (el) => {
      const b = findDayBlock(el.dataset.key);
      if (!(await confirmBox(`Видалити «${b.title}» з ${fmtDate(S.day.date)}?`))) return;
      await run(() => api("DELETE", `override/${b.override_id}`));
      closeSheets(); await afterChange();
    },
    "extra-new": () => {
      const now = nowMin();
      const s = S.day.is_today ? Math.min(1380, Math.ceil(now / 30) * 30) : 600;
      S.form = { mode: "extra-new", date: S.day.date, title: "", emoji: "", color: COLORS[1], start: fromMin(s), end: fromMin(Math.min(1439, s + 60)) };
      openSheet(blockForm);
    },
    "tpl-edit-from-day": async (el) => {
      const b = findDayBlock(el.dataset.key);
      await run(loadSchedule);
      S.tplDay = S.day.weekday;
      const tb = (S.schedule[String(S.tplDay)] || []).find((x) => x.id === b.block_id);
      if (!tb) return;
      S.form = { mode: "tpl-edit", id: tb.id, title: tb.title, emoji: tb.emoji, color: tb.color, start: tb.start, end: tb.end === "24:00" ? "23:59" : tb.end };
      replaceSheet(tplEditor); openSheet(blockForm);
    },

    // template
    "tpl-open": async () => {
      await run(loadSchedule);
      S.tplDay = S.day ? S.day.weekday : weekdayOf(S.today);
      openSheet(tplEditor);
    },
    "tpl-day": (el) => { S.tplDay = +el.dataset.v; haptic(); renderSheet(); },
    "tpl-new": () => {
      const list = (S.schedule[String(S.tplDay)] || []);
      const last = list.length ? list[list.length - 1].end : "08:00";
      const s = last === "24:00" ? 1380 : Math.min(1380, toMin(last));
      S.form = { mode: "tpl-new", title: "", emoji: "", color: COLORS[0], start: fromMin(s), end: fromMin(Math.min(1439, s + 60)), days: String(S.tplDay) };
      openSheet(blockForm);
    },
    "tpl-edit": (el) => {
      const b = (S.schedule[String(S.tplDay)] || []).find((x) => x.id === +el.dataset.id);
      S.form = { mode: "tpl-edit", id: b.id, title: b.title, emoji: b.emoji, color: b.color, start: b.start, end: b.end === "24:00" ? "23:59" : b.end };
      openSheet(blockForm);
    },
    "tpl-delete": async () => {
      if (!(await confirmBox(`Видалити «${S.form.title}» із шаблону (${WDF[S.tplDay]})?`))) return;
      await run(() => api("DELETE", `schedule/${S.form.id}`));
      await run(loadSchedule); popSheet(); await afterChange();
    },
    "tpl-copy": () => { S.form = { days: "" }; openSheet(tplCopySheet); },
    "tpl-copy-go": async () => {
      const to = S.form.days.split("").map(Number).filter((d) => d !== S.tplDay);
      if (!to.length) return toast("Обери дні");
      if (!(await confirmBox(`Замінити розклад: ${to.map((d) => WD[d]).join(", ")}?`))) return;
      await run(() => api("POST", "schedule/copy", { from: S.tplDay, to }));
      haptic("success"); toast("Скопійовано");
      await run(loadSchedule); popSheet(); await afterChange();
    },

    // form helpers
    "form-set": (el) => { S.form[el.dataset.k] = el.dataset.v; haptic(); renderSheet(); },
    "form-day": (el) => {
      const v = el.dataset.v, d = S.form.days || "";
      S.form.days = d.includes(v) ? d.replace(v, "") : d + v;
      haptic(); renderSheet();
    },

    // tasks
    "task-status": async (el) => { S.taskStatus = el.dataset.v; await run(loadTasks); render(); },
    "task-cat": (el) => { S.taskCat = el.dataset.v; render(); },
    "task-new": (el) => {
      const d = el.dataset.date;
      S.form = { title: "", description: "", priority: "medium", category: "personal", recurrence: "none", deadline: d ? d + "T18:00" : "" };
      openSheet(taskForm);
    },
    "task-open": (el) => {
      const id = +el.dataset.id;
      const pool = [...S.tasks, ...(S.day ? [...S.day.tasks.due, ...S.day.tasks.overdue, ...S.day.tasks.done] : [])];
      const t = pool.find((x) => x.id === id);
      if (!t) return;
      S.form = { id: t.id, status: t.status, title: t.title, description: t.description || "", priority: t.priority, category: t.category,
        recurrence: t.recurrence || "none", deadline: t.deadline ? t.deadline.slice(0, 16) : "" };
      openSheet(taskForm);
    },
    "task-done": async (el) => { if (S.sheetStack.length) closeSheets(); await toggleTaskDone(+el.dataset.id); },
    "task-undo": async (el) => { await run(() => api("POST", `tasks/${el.dataset.id}/undo`, {})); haptic(); await afterChange(); },
    "task-delete": async (el) => {
      if (!(await confirmBox("Видалити завдання?"))) return;
      await run(() => api("DELETE", `tasks/${el.dataset.id}`));
      closeSheets(); toast("Видалено"); await afterChange();
    },

    // habits
    "habit-new": () => { S.form = { title: "", emoji: "", days: "0123456" }; openSheet(habitForm); },
    "habit-quick": async (el) => {
      await run(() => api("POST", "habits", { title: el.dataset.t, emoji: el.dataset.e, days: "0123456" }));
      haptic("success"); await afterChange();
    },
    "habit-open": (el) => {
      const h = S.habits.find((x) => x.id === +el.dataset.id);
      S.form = { id: h.id, title: h.title, emoji: h.emoji, days: h.days };
      openSheet(habitForm);
    },
    "habit-toggle": async (el) => {
      const r = await run(() => api("POST", `habits/${el.dataset.id}/toggle`, { date: el.dataset.date }));
      haptic(r.done ? "success" : "light");
      await afterChange();
    },
    "habit-delete": async (el) => {
      if (!(await confirmBox("Видалити звичку разом з історією?"))) return;
      await run(() => api("DELETE", `habits/${el.dataset.id}`));
      closeSheets(); await afterChange();
    },

    // stats
    "stats-days": async (el) => { S.statsDays = +el.dataset.v; S.stats = null; render(); await run(loadStats); render(); },
  };

  // ---------------------------------------------------------------- form submit
  const submits = {
    quickadd: async () => {
      const input = $("#qa");
      const title = input.value.trim();
      if (!title) return;
      await run(() => api("POST", "tasks", { title }));
      input.value = "";
      haptic("success"); toast("Додано");
      await afterChange();
    },
    task: async () => {
      const f = S.form;
      if (!f.title || !f.title.trim()) return toast("Потрібна назва");
      const body = { title: f.title, description: f.description, priority: f.priority, category: f.category, recurrence: f.recurrence, deadline: f.deadline || null };
      if (f.id) await run(() => api("PATCH", `tasks/${f.id}`, body));
      else await run(() => api("POST", "tasks", body));
      haptic("success"); closeSheets(); toast(f.id ? "Збережено" : "Додано");
      await afterChange();
    },
    habit: async () => {
      const f = S.form;
      if (!f.title || !f.title.trim()) return toast("Потрібна назва");
      if (!f.days) return toast("Обери хоча б один день");
      const body = { title: f.title, emoji: f.emoji, days: f.days };
      if (f.id) await run(() => api("PATCH", `habits/${f.id}`, body));
      else await run(() => api("POST", "habits", body));
      haptic("success"); closeSheets();
      await afterChange();
    },
    block: async () => {
      const f = S.form;
      if (!f.title || !f.title.trim()) return toast("Потрібна назва");
      if (!f.start || !f.end) return toast("Вкажи час");
      const body = { title: f.title, emoji: f.emoji || "", color: f.color || "", start: f.start, end: f.end };
      if (f.mode === "tpl-new") {
        if (!f.days) return toast("Обери дні");
        await run(() => api("POST", "schedule", { ...body, weekday: f.days.split("").map(Number) }));
      } else if (f.mode === "tpl-edit") {
        await run(() => api("PATCH", `schedule/${f.id}`, body));
      } else if (f.mode === "ovr-edit") {
        await run(() => api("POST", `day/${f.date}/override`, { ...body, block_id: f.block_id }));
      } else if (f.mode === "extra-new") {
        await run(() => api("POST", `day/${f.date}/override`, body));
      } else if (f.mode === "extra-edit") {
        await run(() => api("POST", `day/${f.date}/override`, body));
        await run(() => api("DELETE", `override/${f.override_id}`));
      }
      haptic("success");
      if (f.mode.startsWith("tpl")) { await run(loadSchedule); popSheet(); }
      else closeSheets();
      await afterChange();
    },
  };

  // ---------------------------------------------------------------- tooltips (charts)
  let tipTimer = null;
  function showTip(el) {
    const tip = $("#tip"), r = el.getBoundingClientRect();
    tip.textContent = el.dataset.tip;
    tip.style.left = Math.max(60, Math.min(window.innerWidth - 60, r.left + r.width / 2)) + "px";
    tip.style.top = r.top + 18 + "px";
    tip.hidden = false;
    document.querySelectorAll(".chart .bar.sel").forEach((b) => b.classList.remove("sel"));
    if (el.nextElementSibling) el.nextElementSibling.classList.add("sel");
    clearTimeout(tipTimer);
    tipTimer = setTimeout(hideTip, 2500);
  }
  function hideTip() {
    $("#tip").hidden = true;
    document.querySelectorAll(".chart .bar.sel").forEach((b) => b.classList.remove("sel"));
  }

  // ---------------------------------------------------------------- events
  let busy = false;
  document.addEventListener("click", async (e) => {
    const tipEl = e.target.closest("[data-tip]");
    if (tipEl) { showTip(tipEl); return; }
    hideTip();
    if (e.target.id === "sheet-backdrop") { closeSheets(); return; }
    const tabBtn = e.target.closest("#tabbar button");
    if (tabBtn) { switchTab(tabBtn.dataset.tab); return; }
    const el = e.target.closest("[data-act]");
    if (!el || el.disabled) return;
    const fn = actions[el.dataset.act];
    if (!fn) return;
    e.preventDefault();
    if (busy) return;
    busy = true;
    try { await fn(el); } catch (_) { /* toast already shown */ } finally { busy = false; }
  });

  document.addEventListener("input", (e) => {
    const k = e.target.dataset && e.target.dataset.f;
    if (k) S.form[k] = e.target.value;
  });
  document.addEventListener("change", (e) => {
    const k = e.target.dataset && e.target.dataset.f;
    if (k) {
      S.form[k] = e.target.value;
      if (k === "deadline") renderSheet(); // refresh chip highlight + reminder note
    }
  });

  document.addEventListener("submit", async (e) => {
    const name = e.target.dataset.form;
    if (!name || !submits[name]) return;
    e.preventDefault();
    if (busy) return;
    busy = true;
    try { await submits[name](); } catch (_) { /* shown */ } finally { busy = false; }
  });

  async function switchTab(tab) {
    if (tab === S.tab) return;
    S.tab = tab;
    haptic();
    window.scrollTo(0, 0);
    render();
    try { await refresh(); } catch (e) { toast(e.message); }
  }

  // ---------------------------------------------------------------- theme + boot
  function applyTheme() {
    const light = tg && tg.colorScheme === "light";
    document.documentElement.dataset.theme = light ? "light" : "dark";
    try {
      const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
      if (tg && tg.isVersionAtLeast && tg.isVersionAtLeast("6.1")) {
        tg.setHeaderColor(bg);
        tg.setBackgroundColor(bg);
      }
      if (tg && tg.isVersionAtLeast && tg.isVersionAtLeast("7.10") && tg.setBottomBarColor) tg.setBottomBarColor(bg);
    } catch (_) { /* old client */ }
  }

  function fatal(title, text) {
    $("#view").innerHTML = `<div class="fatal"><b>${esc(title)}</b>${esc(text)}</div>`;
    $("#tabbar").hidden = true;
  }

  async function boot() {
    if (tg) {
      tg.ready();
      tg.expand();
      try { if (tg.isVersionAtLeast("7.7")) tg.disableVerticalSwipes(); } catch (_) { /* old client */ }
      tg.onEvent("themeChanged", applyTheme);
      if (tg.BackButton) tg.BackButton.onClick(() => (S.sheetStack.length > 1 ? popSheet() : closeSheets()));
    }
    applyTheme();
    if (!tg || !tg.initData) {
      fatal("Відкрий через Telegram", "Ця апка працює всередині Telegram-бота — кнопка «📱 Апка» біля поля вводу.");
      return;
    }
    render();
    try {
      await loadDay();
      render();
    } catch (e) {
      fatal("Не вдалось завантажити", e.message);
      return;
    }
    setInterval(() => {
      renderClock();
      if (S.tab === "day" && S.day && S.day.is_today && !S.sheetStack.length) $("#view").innerHTML = renderDay();
    }, 30000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && !S.sheetStack.length) refresh().catch(() => {});
    });
  }

  boot();
})();
