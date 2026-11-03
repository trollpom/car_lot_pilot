"use strict";

const $ = (s) => document.querySelector(s);
const AUTH_KEY = "ab_admin_token";

const els = {
  grid: $("#lotsGrid"),
  search: $("#fSearch"),
  btnSearch: $("#btnSearch"),
  vin: $("#fVin"),
  category: $("#fCategory"),
  region: $("#fRegion"),
  sale: $("#fSale"),
  sort: $("#fSort"),
  source: $("#fSource"),
  emailSt: $("#fEmail"),
  make: $("#fMake"),
  model: $("#fModel"),
  yearFrom: $("#fYearFrom"),
  yearTo: $("#fYearTo"),
  tradeKind: $("#fTradeKind"),
  tradeType: $("#fTradeType"),
  status: $("#fStatus"),
  hasPhoto: $("#fHasPhoto"),
  reset: $("#btnReset"),
  pagination: $("#pagination"),
  statsTotal: $("#statTotal"),
  statsPhoto: $("#statPhoto"),
  statsCars: $("#statCars"),
  statsSpecial: $("#statSpecial"),
  resultsCount: $("#resultsCount"),
  resultsTitle: $("#resultsTitle"),
  spinner: $("#spinner"),
  overlay: $("#overlay"),
  modalBody: $("#modalBody"),
  btnClose: $("#btnClose"),
  btnScrape: $("#btnScrape"),
  btnScrapePlatforms: $("#btnScrapePlatforms"),
  btnScrapeTorgi: $("#btnScrapeTorgi"),
  btnEmailSend: $("#btnEmailSend"),
  btnEmailCheck: $("#btnEmailCheck"),
  scrapeStatus: $("#scrapeStatus"),
  emailConfig: $("#emailConfig"),
  eventsList: $("#eventsList"),
  eventsUpdated: $("#eventsUpdated"),
  eventsCount: $("#eventsCount"),
  eventsLogLink: $("#eventsLogLink"),
  adminPanel: $("#adminPanel"),
  adminBadge: $("#adminBadge"),
  btnAdmin: $("#btnAdmin"),
  btnLogout: $("#btnLogout"),
  loginOverlay: $("#loginOverlay"),
  btnLoginClose: $("#btnLoginClose"),
  adminPassword: $("#adminPassword"),
  btnLoginSubmit: $("#btnLoginSubmit"),
  loginError: $("#loginError"),
};

const state = { page: 1, pageSize: 24, pages: 1, current: null };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmtPrice = (n, cur) => {
  if (n == null) return "цена не указана";
  const s = Number(n).toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  const c = cur && cur !== "RUB" ? ` ${cur}` : " ₽";
  return `${s}${c}`;
};

const fmtDate = (s) => {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d.getTime())) return String(s);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

function parseSpecs(raw) {
  if (!raw) return {};
  try { return typeof raw === "string" ? JSON.parse(raw) : raw; } catch (e) { return {}; }
}

function shortTrade(f) {
  const s = (f || "").toLowerCase();
  if (s.includes("публичного предложения")) return "Публичное предложение";
  if (s.includes("открытый аукцион")) return "Открытый аукцион";
  if (s.includes("закрытый аукцион")) return "Закрытый аукцион";
  if (s.includes("открытый конкурс")) return "Открытый конкурс";
  if (s.includes("закрытый конкурс")) return "Закрытый конкурс";
  if (s.includes("прямая продажа")) return "Прямая продажа";
  return f || "";
}

function statusClassOf(s) {
  s = (s || "").toLowerCase();
  if (s.includes("приём") || s.includes("прием")) return "s-open";
  if (s.includes("объявлен")) return "s-announced";
  if (s.includes("продан") || s.includes("заверш") || s.includes("итог")) return "s-done";
  return "";
}

function carName(lot, specs) {
  const parts = [];
  const mm = lot.make_model || specs.make_model;
  if (mm) parts.push(mm);
  const year = specs.year || lot.year;
  if (year) parts.push(`${year} г.`);
  if (specs.engine_power) parts.push(specs.engine_power);
  else if (specs.engine_volume) parts.push(`${specs.engine_volume} см³`);
  if (parts.length) return parts.join(", ");
  return lot.title || "Имущество должника";
}

// ---------------------------------------------------------------------------
// Auth (администратор)
// ---------------------------------------------------------------------------
const token = () => localStorage.getItem(AUTH_KEY) || "";
const isAdmin = () => !!token();

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (token()) headers["X-Admin-Token"] = token();
  const r = await fetch(path, Object.assign({}, opts, { headers }));
  if (r.status === 401) {
    localStorage.removeItem(AUTH_KEY);
    applyAdminUI();
    throw new Error("Требуется вход администратора");
  }
  if (!r.ok) {
    const error = await r.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : `Ошибка входных данных или запроса (HTTP ${r.status})`);
  }
  return r.json();
}

function applyAdminUI() {
  const on = isAdmin();
  if (!on) {
    window.LotAnalysis.unmount(); document.querySelector("#lotAnalysis")?.remove();
    window.VehicleChecks.unmount(); document.querySelector("#vehicleChecks")?.remove();
  }
  document.querySelectorAll(".admin-only").forEach((el) => el.classList.toggle("hidden", !on));
  els.adminPanel.classList.toggle("hidden", !on);
  els.adminBadge.classList.toggle("hidden", !on);
  els.btnAdmin.classList.toggle("hidden", on);
  if (on) {
    els.eventsLogLink.href = `/api/events/log?tail=5000&token=${encodeURIComponent(token())}`;
    loadEmailStatus();
    loadEvents();
    pollStatus();
  }
}

async function adminLogin() {
  const pass = els.adminPassword.value;
  try {
    const r = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pass }),
    });
    if (!r.ok) { els.loginError.textContent = "Неверный пароль"; return; }
    const d = await r.json();
    localStorage.setItem(AUTH_KEY, d.token);
    els.adminPassword.value = "";
    els.loginError.textContent = "";
    els.loginOverlay.classList.add("hidden");
    applyAdminUI();
  } catch (e) {
    els.loginError.textContent = "Ошибка входа";
  }
}

function adminLogout() {
  const t = token();
  if (t) fetch("/api/admin/logout", { method: "POST", headers: { "X-Admin-Token": t } }).catch(() => {});
  localStorage.removeItem(AUTH_KEY);
  applyAdminUI();
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
async function loadOptions() {
  try {
    const [cats, regs, sales, makes, years, kinds, ttypes, statuses] = await Promise.all([
      api("/api/categories"), api("/api/regions"), api("/api/sale_methods"),
      api("/api/makes"), api("/api/years"), api("/api/trade_kinds"),
      api("/api/trade_types"), api("/api/statuses"),
    ]);
    const add = (sel, val, label) => {
      const o = document.createElement("option");
      o.value = val; o.textContent = label; sel.appendChild(o);
    };
    for (const c of cats.items || []) add(els.category, c.value, `${c.value} (${c.cnt})`);
    for (const r of regs.items || []) add(els.region, r.value, r.value);
    for (const s of sales.items || []) add(els.sale, s.value, `${s.value} (${s.cnt})`);
    for (const m of makes.items || []) add(els.make, m.value, `${m.value} (${m.cnt})`);
    for (const y of years.items || []) {
      for (const sel of [els.yearFrom, els.yearTo]) add(sel, y.value, `${y.value} (${y.cnt})`);
    }
    for (const k of kinds.items || []) add(els.tradeKind, k.value, `${k.label} (${k.cnt})`);
    for (const t of ttypes.items || []) add(els.tradeType, t.value, `${t.value} (${t.cnt})`);
    for (const s of statuses.items || []) add(els.status, s.value, `${s.value} (${s.cnt})`);
  } catch (e) { console.warn(e); }
}

async function loadModels(make) {
  els.model.innerHTML = '<option value="">любая</option>';
  els.model.disabled = !make;
  if (!make) return;
  try {
    const d = await api(`/api/models?make=${encodeURIComponent(make)}`);
    for (const m of d.items || []) {
      const o = document.createElement("option");
      o.value = m.value; o.textContent = `${m.value} (${m.cnt})`;
      els.model.appendChild(o);
    }
  } catch (e) { console.warn(e); }
}

async function loadStats() {
  try {
    const s = await api("/api/stats");
    els.statsTotal.textContent = s.total_lots;
    els.statsPhoto.textContent = s.with_photo;
    els.statsCars.textContent = s.categories["Легковые автомобили"] || 0;
    els.statsSpecial.textContent = (s.categories["Спецтехника"] || 0) + (s.categories["Автобусы/Грузовой"] || 0);
  } catch (e) { console.warn(e); }
}

const EVENT_KIND = {
  email_sent: "Email", email_resent: "Повтор", response: "Ответ", photos: "Фото",
  bounce: "Не доставлено", lot_new: "Новый лот", scrape: "Скрейпинг", email_batch: "Рассылка",
  status_update: "Статус", price_update: "Цена",
};

function fmtTs(ts) {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts || "";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())} ${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`;
}

async function loadEvents() {
  if (!isAdmin()) return;
  try {
    const d = await api("/api/events?today=1&limit=200");
    const items = d.items || [];
    els.eventsCount.textContent = d.today_count != null ? `— сегодня: ${d.today_count}, всего: ${d.total_count}` : "";
    els.eventsList.innerHTML = items.length
      ? items.map((e) => {
          const kind = (e.kind || "").replace(/[^a-z_]/g, "");
          const link = e.lot_id ? ` <a href="#" data-lot="${e.lot_id}">открыть лот</a>` : "";
          return `<div class="ev"><span class="ev-ts">${fmtTs(e.ts)}</span>` +
            `<span class="ev-kind ev-${kind}">${esc(EVENT_KIND[e.kind] || e.kind || "")}</span>` +
            `<span class="ev-msg">${esc(e.message || "")}</span>${link}</div>`;
        }).join("")
      : '<div class="muted">сегодня действий ещё не было</div>';
    els.eventsList.querySelectorAll("[data-lot]").forEach((a) => {
      a.onclick = (ev) => { ev.preventDefault(); openLot(Number(a.dataset.lot)); };
    });
    els.eventsUpdated.textContent = "обновлено " + new Date().toLocaleTimeString("ru-RU");
  } catch (e) { els.eventsList.textContent = "ошибка загрузки журнала"; }
}

async function loadEmailStatus() {
  if (!isAdmin()) return;
  try {
    const s = await api("/api/email/status");
    const cfg = [s.smtp_configured ? "SMTP ✓" : "SMTP ✗", s.imap_configured ? "IMAP ✓" : "IMAP ✗"];
    if (s.secondary_enabled) {
      cfg.push((s.secondary_smtp_configured ? "2-я почта ✓" : "2-я почта ✗") +
        (s.secondary_from ? ` (${esc(s.secondary_from)})` : ""));
    }
    const auto = [];
    if (s.auto_send) auto.push(`рассылка каждые ${s.send_interval_min} мин`);
    if (s.auto_check) auto.push(`проверка каждые ${s.check_interval_min} мин`);
    els.emailConfig.innerHTML =
      cfg.join(" · ") + (auto.length ? ` · ${esc(auto.join(", "))}` : "") +
      ` · ждут запроса (без фото): <b>${s.lots_waiting_no_photo}</b>` +
      (s.lots_reminder ? ` · напоминание: ${s.lots_reminder}` : "") +
      ` · отправлено сегодня: <b>${s.sent_today}</b>/${s.daily_cap_today ?? s.daily_limit}` +
      (s.warmup_day != null ? ` (прогрев, день ${s.warmup_day + 1})` : "") +
      ` · ответов: <b>${s.lots_responded}</b>` +
      (s.lots_bounced ? ` · недоставлено: <b class="warn">${s.lots_bounced}</b>` : "");
    if (!s.smtp_configured || !s.imap_configured) {
      els.emailConfig.innerHTML += ' <b class="warn">— заполните EMAIL_SMTP_*/EMAIL_IMAP_* в .env</b>';
    }
  } catch (e) { els.emailConfig.textContent = "—"; }
}

function cardHtml(lot) {
  const specs = parseSpecs(lot.specs);
  const name = carName(lot, specs);
  const img = lot.first_thumb
    ? `<img loading="lazy" src="/data/${esc(lot.first_thumb)}" alt="">`
    : `<span class="no-photo">фото нет</span>`;
  const photoCount = lot.photos_found ? `<span class="lot-photo-count">${lot.photos_found} фото</span>` : "";
  const statusBadge = lot.status ? `<span class="lot-status ${statusClassOf(lot.status)}">${esc(lot.status)}</span>` : "";
  const priceNow = lot.current_price != null ? lot.current_price : lot.price_amount;
  const priceStart = lot.start_price != null
    ? `<div class="price-start">${fmtPrice(lot.start_price)}</div><div class="price-cap">начальная цена</div>`
    : "";
  const badges = [];
  if (lot.trade_form) badges.push(`<span class="badge">${esc(shortTrade(lot.trade_form))}</span>`);
  if (lot.email_status === "responded") badges.push('<span class="badge email-responded">Есть ответ</span>');
  else if (lot.email_status === "sent") badges.push('<span class="badge email-sent">Запрос отправлен</span>');
  const chips = [];
  if (lot.vin) chips.push(`<span class="chip chip-vin">VIN: ${esc(lot.vin)}</span>`);
  if (lot.debtor) chips.push(`<span class="chip">должник: ${esc(lot.debtor)}</span>`);
  if (lot.region) chips.push(`<span class="chip">${esc(lot.region)}</span>`);
  if (lot.days_remaining) chips.push(`<span class="chip">${esc(lot.days_remaining)}</span>`);
  const end = lot.end_date ? `<div class="lot-date">до ${esc(fmtDate(lot.end_date))}</div>` : "";
  return `
  <article class="lot-card" data-id="${lot.id}">
    <div class="lot-photo">${statusBadge}${img}${photoCount}</div>
    <div class="lot-info">
      <h3 class="lot-name">${esc(name)}</h3>
      <div class="lot-sub">№ ${esc(lot.lot_no || "—")} ${lot.trade_type ? "· " + esc(lot.trade_type) : ""} ${badges.join(" ")}</div>
      <p class="lot-desc">${esc(lot.description || lot.title || "")}</p>
      <div class="lot-chips">${chips.join("")}</div>
    </div>
    <div class="lot-price-col">
      <div class="price-now">${fmtPrice(priceNow, lot.price_currency)}</div>
      <div class="price-cap">текущая цена</div>
      ${priceStart}
      <button class="btn btn-primary">Смотреть</button>
      ${end}
    </div>
  </article>`;
}

async function loadLots() {
  els.spinner.classList.remove("hidden");
  els.grid.style.opacity = ".45";
  const q = new URLSearchParams({
    search: els.search.value.trim(),
    category: els.category.value,
    region: els.region.value,
    sale_method: els.sale.value,
    sort: els.sort.value,
    vin: els.vin.value.trim(),
    source: els.source.value,
    make: els.make.value,
    model: els.model.value,
    trade_kind: els.tradeKind.value,
    trade_type: els.tradeType.value,
    status: els.status.value,
    page: state.page,
    page_size: state.pageSize,
  });
  if (isAdmin()) q.set("email_status", els.emailSt.value);
  if (els.yearFrom.value) q.set("year_from", els.yearFrom.value);
  if (els.yearTo.value) q.set("year_to", els.yearTo.value);
  if (els.hasPhoto.checked) q.set("has_photo", "true");
  try {
    const d = await api(`/api/lots?${q.toString()}`);
    state.pages = d.pages;
    els.resultsCount.textContent = `найдено: ${d.total}`;
    els.grid.innerHTML = d.items.length
      ? d.items.map(cardHtml).join("")
      : '<div class="muted" style="text-align:center;padding:30px">Ничего не найдено</div>';
    renderPagination();
    bindCards();
  } catch (e) {
    els.grid.innerHTML = `<div class="muted" style="text-align:center;padding:30px">Ошибка загрузки: ${esc(e.message)}</div>`;
  } finally {
    els.grid.style.opacity = "1";
    els.spinner.classList.add("hidden");
  }
}

function renderPagination() {
  const p = els.pagination;
  p.innerHTML = "";
  if (state.pages <= 1 && state.page <= 1) return;
  const mk = (n, label) => {
    const b = document.createElement("button");
    b.textContent = label ?? n;
    if (n === state.page) b.className = "active";
    b.onclick = () => { state.page = n; loadLots(); window.scrollTo({ top: 380, behavior: "smooth" }); };
    p.appendChild(b);
  };
  if (state.page > 1) mk(state.page - 1, "←");
  const from = Math.max(1, state.page - 2), to = Math.min(state.pages, state.page + 2);
  for (let i = from; i <= to; i++) mk(i);
  if (state.page < state.pages) mk(state.page + 1, "→");
}

function bindCards() {
  els.grid.querySelectorAll(".lot-card").forEach((el) => {
    el.onclick = () => openLot(Number(el.dataset.id));
  });
}

async function openLot(id) {
  try {
    const lot = await api(`/api/lots/${id}`);
    renderDetail(lot);
    els.overlay.classList.remove("hidden");
  } catch (e) { alert("Ошибка: " + e.message); }
}

function priceChartHtml(history, decrease) {
  const pts = history.map((h) => ({ t: new Date(String(h.recorded_at).replace(" ", "T")), p: Number(h.price) }));
  const n = pts.length;
  const prices = pts.map((p) => p.p);
  const min = Math.min(...prices), max = Math.max(...prices);
  const span = max - min || 1;
  const W = 620, H = 180, P = 18;
  const x = (i) => (n === 1 ? W / 2 : P + ((W - 2 * P) * i) / (n - 1));
  const y = (v) => H - P - ((H - 2 * P) * (v - min)) / span;
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.p).toFixed(1)}`).join(" ");
  const dots = pts.map((p, i) =>
    `<circle cx="${x(i).toFixed(1)}" cy="${y(p.p).toFixed(1)}" r="2.6" fill="var(--accent)"/>`).join("");
  const area = `${line} L${x(n - 1).toFixed(1)},${H - P} L${x(0).toFixed(1)},${H - P} Z`;
  const first = pts[0], last = pts[n - 1];
  const dec = decrease && last.p < first.p;
  const color = dec ? "var(--red)" : last.p > first.p ? "var(--green)" : "var(--accent)";
  const fmt = (v) => (v >= 1e6 ? (v / 1e6).toFixed(2).replace(/\.?0+$/, "") + " млн ₽"
    : Math.round(v).toLocaleString("ru-RU") + " ₽");
  return `<div class="price-chart">
    <div class="chart-summary">
      <span class="chart-big" style="color:${color}">${fmt(last.p)}</span>
      <span class="muted">сегодня</span>
      <span class="chart-old">старт ${fmt(first.p)} · ${first.t.toLocaleDateString("ru-RU")}</span>
    </div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Динамика цены">
      <path d="${area}" fill="${color}" opacity=".08"/>
      <path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>
      ${dots}
    </svg>
    <div class="chart-labels">
      <span>${first.t.toLocaleDateString("ru-RU")}</span>
      <span>${last.t.toLocaleDateString("ru-RU")}</span>
    </div>
  </div>`;
}

function renderDetail(lot) {
  window.LotAnalysis.unmount();
  window.VehicleChecks.unmount();
  const photos = (lot.photos || []).filter((p) => p.local_path);
  const gallery = photos.length
    ? `<div class="gallery">${photos.map((p) =>
        `<a href="/data/${esc(p.local_path)}" target="_blank"><img loading="lazy" src="/data/${esc(p.thumb_path || p.local_path)}"></a>`).join("")}
      </div><div class="muted">источники: ${[...new Set(photos.map((p) => p.source_site).filter(Boolean))].join(", ") || "—"}</div>`
    : `<div class="gallery-empty">Фото пока не найдены.</div>`;
  const row = (k, v) => (v ? `<dt>${k}</dt><dd>${esc(v)}</dd>` : "");
  const priceRow = (k, v) => (v != null ? `<dt>${k}</dt><dd>${fmtPrice(v)}</dd>` : "");

  let conditionHtml = "";
  if (lot.condition_info) {
    try {
      const cond = typeof lot.condition_info === "string" ? JSON.parse(lot.condition_info) : lot.condition_info;
      conditionHtml = `
        <h3>Состояние автомобиля</h3>
        <div class="detail-grid">
          ${row("Общее описание", cond.condition_summary)}
          ${row("Пробег", cond.mileage)}
          ${row("Двигатель", cond.engine_state)}
          ${row("Кузов", cond.body_state)}
          ${row("Салон", cond.interior_state)}
          ${row("Дефекты", cond.defects)}
          ${row("Документы", cond.documents_available)}
          ${row("Осмотр", cond.inspection_possible)}
        </div>`;
    } catch (e) { /* ignore */ }
  }

  let specsHtml = "";
  const sp = lot.specs_data || {};
  const specRow = (k, v) => (v != null && v !== "" ? `<dt>${k}</dt><dd>${esc(v)}</dd>` : "");
  const power = [sp.engine_power, sp.engine_power_kw].filter(Boolean).join(" / ");
  const specRows =
    specRow("Год выпуска", sp.year) + specRow("Цвет", sp.color) +
    specRow("Модель двигателя", sp.engine_model) + specRow("Тип двигателя", sp.engine_type) +
    specRow("№ двигателя", sp.engine_no) + specRow("Мощность", power) +
    specRow("Объём, куб.см", sp.engine_volume) + specRow("Кузов №", sp.body_no) +
    specRow("Шасси/рама №", sp.chassis_no) + specRow("Макс. масса, кг", sp.mass_max) +
    specRow("Масса без нагрузки, кг", sp.mass_curb) + specRow("Гос. номер", sp.gos_number) +
    specRow("ПТС", sp.pts) + specRow("СТС", sp.sts);
  if (specRows) specsHtml = `<h3>Технические характеристики</h3><div class="detail-grid">${specRows}</div>`;

  let emailHtml = "";
  if (isAdmin() && lot.email_status && lot.email_status !== "not_sent") {
    const statusLabel = lot.email_status === "sent" ? "Запрос отправлен" :
                        lot.email_status === "responded" ? "Получен ответ" :
                        lot.email_status === "bounced" ? "Не доставлено" : lot.email_status;
    const sentDate = lot.email_sent_at ? ` · ${lot.email_sent_at.replace("T", " ").slice(0, 16)}` : "";
    emailHtml = `<div class="email-status"><b>Email:</b> ${esc(statusLabel)}${sentDate}</div>`;
  }

  const adminButtons = isAdmin() ? `
      ${lot.vin ? `<button class="btn btn-primary" id="btnSearchPhotos">Найти фото по VIN</button>` : ""}
      ${lot.contact_email ? `<button class="btn btn-email" id="btnSendEmail">Отправить запрос</button>` : ""}` : "";

  const isPublicOffer = (lot.trade_form || lot.trade_type || "").toLowerCase().includes("публичного предложения");
  const daysLabel = isPublicOffer ? "Дней до снижения цены" : "Дней до торгов";
  let chartHtml = "";
  const history = lot.price_history || [];
  if (history.length >= 2) {
    const dec = history[history.length - 1].price < history[0].price;
    chartHtml = `<h3>Динамика цены</h3>${priceChartHtml(history, dec)}`;
  } else if (isPublicOffer && history.length === 1) {
    chartHtml = `<h3>Динамика цены</h3><div class="muted">Идёт сбор истории цен — обновляется ежедневно.</div>`;
  }

  els.modalBody.innerHTML = `
    <h2>${esc(carName(lot, sp))}</h2>
    ${emailHtml}
    <div class="detail-grid">
      ${row("№ лота", lot.lot_no)}
      ${row("Категория", lot.category)}
      ${row("Тип торгов", lot.trade_type)}
      ${row("Форма торгов", lot.trade_form)}
      ${row("Площадка", lot.marketplace || lot.trade_platform)}
      ${row("Статус", lot.status)}
      ${row(daysLabel, lot.days_remaining)}
      ${row("Должник", lot.debtor)}
      ${row("Управляющий", lot.trustee)}
      ${row("Способ реализации", lot.sale_method)}
      ${priceRow("Начальная цена", lot.start_price)}
      ${priceRow("Текущая цена", lot.current_price || lot.price_amount)}
      ${row("VIN", lot.vin)}
      ${row("Регион", lot.region)}
      ${row("Начало приёма заявок", lot.publish_date)}
      ${row("Окончание приёма заявок", lot.end_date)}
      ${row("Контактное лицо", lot.contact_name)}
      ${row("Телефон", lot.contact_phone)}
      ${row("Email", lot.contact_email)}
    </div>
    ${chartHtml}
    ${specsHtml}
    <h3>Фото</h3>
    ${gallery}
    <h3>Описание</h3>
    <div class="detail-desc">${esc(lot.description || "нет описания")}</div>
    ${lot.acquaintance_order ? `<h3>Порядок ознакомления</h3><div class="detail-desc">${esc(lot.acquaintance_order)}</div>` : ""}
    ${conditionHtml}
    ${isAdmin() ? window.VehicleChecks.shell(lot) : ""}
    ${isAdmin() ? window.LotAnalysis.shell(lot) : ""}
    <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
      ${adminButtons}
      ${lot.source_url ? `<a class="btn btn-ghost" href="${esc(lot.source_url)}" target="_blank">Источник</a>` : ""}
      ${lot.platform_lot_url ? `<a class="btn btn-ghost" href="${esc(lot.platform_lot_url)}" target="_blank">Торговая площадка</a>` : ""}
    </div>`;

  if (isAdmin()) window.LotAnalysis.mount($("#lotAnalysis"), lot, api);
  if (isAdmin()) window.VehicleChecks.mount($("#vehicleChecks"), lot, api);
  const b = $("#btnSearchPhotos");
  if (b) b.onclick = async () => {
    b.disabled = true; b.textContent = "Поиск…";
    try { await api(`/api/lots/${lot.id}/photos/search`, { method: "POST" }); openLot(lot.id); }
    catch (e) { alert("Ошибка: " + e.message); }
    finally { b.disabled = false; b.textContent = "Найти фото по VIN"; }
  };
  const eb = $("#btnSendEmail");
  if (eb) eb.onclick = async () => {
    eb.disabled = true; eb.textContent = "Отправка…";
    try {
      const r = await api(`/api/lots/${lot.id}/email/send`, { method: "POST" });
      if (r.sent) { eb.textContent = "Отправлено ✓"; }
      else { eb.disabled = false; eb.textContent = "Отправить запрос"; alert("Не удалось отправить"); }
    } catch (e) { eb.disabled = false; eb.textContent = "Отправить запрос"; alert("Ошибка: " + e.message); }
  };
}

// ---- фильтры ----
let debounce;
const filterKeys = ["search", "vin", "category", "region", "sale", "sort", "source",
  "yearFrom", "yearTo", "tradeKind", "tradeType", "status"];
filterKeys.forEach((k) => {
  els[k].addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { state.page = 1; loadLots(); }, k === "search" || k === "vin" ? 350 : 60);
  });
});
els.make.addEventListener("change", () => {
  state.page = 1;
  loadModels(els.make.value);
  loadLots();
});
els.model.addEventListener("change", () => { state.page = 1; loadLots(); });
if (els.emailSt) els.emailSt.addEventListener("input", () => { state.page = 1; loadLots(); });
els.hasPhoto.addEventListener("change", () => { state.page = 1; loadLots(); });
els.btnSearch.onclick = () => { state.page = 1; loadLots(); };
els.search.addEventListener("keydown", (e) => { if (e.key === "Enter") { state.page = 1; loadLots(); } });
els.reset.addEventListener("click", () => {
  [...filterKeys, "emailSt"].forEach((k) => { if (els[k]) els[k].value = ""; });
  els.make.value = ""; els.model.value = "";
  els.sort.value = "published_desc"; els.hasPhoto.checked = false;
  state.page = 1; loadModels(""); loadLots();
});

// ---- admin: задачи ----
async function pollStatus() {
  if (!isAdmin()) return;
  try {
    const d = await api("/api/scrape/status");
    const chip = els.scrapeStatus;
    if (d.running) {
      chip.textContent = "⏳ выполняется задача…";
      chip.className = "status-chip";
      [els.btnScrape, els.btnScrapePlatforms, els.btnScrapeTorgi, els.btnEmailSend, els.btnEmailCheck]
        .forEach((b) => (b.disabled = true));
      setTimeout(pollStatus, 3000);
    } else {
      [els.btnScrape, els.btnScrapePlatforms, els.btnScrapeTorgi, els.btnEmailSend, els.btnEmailCheck]
        .forEach((b) => (b.disabled = false));
      if (d.last_run && d.last_run.started_at) {
        const ok = d.last_run.status === "done";
        chip.textContent = `последний запуск: ${ok ? "✓" : "✗"} ${d.last_run.lots_found} лотов, ${d.last_run.photos_found} фото · ${(d.last_run.finished_at || d.last_run.started_at).replace("T", " ").slice(0, 16)}`;
        chip.className = ok ? "status-chip" : "status-chip err";
      } else chip.textContent = "";
    }
  } catch (e) { els.scrapeStatus.textContent = ""; }
}

els.btnScrape.onclick = async () => {
  els.btnScrape.disabled = true; els.scrapeStatus.textContent = "запуск…";
  try {
    await api("/api/scrape/run", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ find_photos: true, all_active: true, max_pages: 200 }) });
    setTimeout(pollStatus, 1500);
  } catch (e) { alert("Ошибка: " + e.message); els.btnScrape.disabled = false; }
};
els.btnScrapePlatforms.onclick = async () => {
  els.btnScrapePlatforms.disabled = true; els.scrapeStatus.textContent = "парсинг площадок…";
  try {
    await api("/api/scrape/platforms", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platforms: ["lot-online.ru", "auction-house.ru"], find_photos: true }) });
    setTimeout(pollStatus, 1500);
  } catch (e) { alert("Ошибка: " + e.message); els.btnScrapePlatforms.disabled = false; }
};
els.btnScrapeTorgi.onclick = async () => {
  els.btnScrapeTorgi.disabled = true; els.scrapeStatus.textContent = "парсинг торгов-россии.рф…";
  try {
    await api("/api/scrape/torgi", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_pages: 30, fetch_details: true, find_photos: false }) });
    setTimeout(pollStatus, 1500);
  } catch (e) { alert("Ошибка: " + e.message); els.btnScrapeTorgi.disabled = false; }
};
els.btnEmailSend.onclick = async () => {
  if (!confirm("Отправить email-запросы управляющим по лотам БЕЗ фото?")) return;
  els.btnEmailSend.disabled = true; els.scrapeStatus.textContent = "отправка email…";
  try { await api("/api/email/send", { method: "POST" }); setTimeout(pollStatus, 1500); }
  catch (e) { alert("Ошибка: " + e.message); els.btnEmailSend.disabled = false; }
};
els.btnEmailCheck.onclick = async () => {
  els.btnEmailCheck.disabled = true; els.scrapeStatus.textContent = "проверка ответов…";
  try { await api("/api/email/check", { method: "POST" }); setTimeout(pollStatus, 1500); }
  catch (e) { alert("Ошибка: " + e.message); els.btnEmailCheck.disabled = false; }
};

// ---- admin: вход ----
els.btnAdmin.onclick = () => { els.loginOverlay.classList.remove("hidden"); els.adminPassword.focus(); };
els.btnLoginClose.onclick = () => els.loginOverlay.classList.add("hidden");
els.loginOverlay.addEventListener("click", (e) => { if (e.target === els.loginOverlay) els.loginOverlay.classList.add("hidden"); });
els.btnLoginSubmit.onclick = adminLogin;
els.adminPassword.addEventListener("keydown", (e) => { if (e.key === "Enter") adminLogin(); });
els.btnLogout.onclick = adminLogout;

const closeLot = () => { window.LotAnalysis.unmount(); window.VehicleChecks.unmount(); els.overlay.classList.add("hidden"); };
els.btnClose.onclick = closeLot;
els.overlay.addEventListener("click", (e) => { if (e.target === els.overlay) closeLot(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeLot(); els.loginOverlay.classList.add("hidden"); }
});

// ---- init ----
(async function init() {
  if (isAdmin()) {
    try { await api("/api/admin/check"); } catch (e) { localStorage.removeItem(AUTH_KEY); }
  }
  applyAdminUI();
  loadOptions();
  loadStats();
  loadLots();
  setInterval(loadStats, 60000);
})();

