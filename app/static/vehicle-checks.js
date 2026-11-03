/* History checks are server-side; no provider URLs, tokens or real demo data in the client. */
"use strict";
window.VehicleChecks = (() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const labels = {ok:"Проверено",empty:"Нет записей",partial:"Неполные данные",unavailable:"Недоступно"};
  const levels = {low:"Низкий по доступным данным",medium:"Умеренный",high:"Высокий",critical:"Критический",unknown:"Не определён"};
  let dispose = () => {};
  const date = value => {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? "неизвестно" : d.toLocaleString("ru-RU");
  };

  function shell(lot) {
    return `<section id="vehicleChecks" class="analysis vehicle-checks admin-only">
      <h3>История автомобиля · ШАССИ</h3>
      <p class="analysis-note">Проверка VIN или госномера через подключённый check_cars. Покрытие зависит от источников; отсутствие записей не гарантирует юридическую чистоту.</p>
      <p data-check-status role="status">Проверка настройки…</p>
      <form data-check-form>
        <div class="analysis-fields">
          <label>VIN лота или госномер<input class="in" name="query" maxlength="40" value="${esc(lot.vin || "")}" autocomplete="off" required></label>
          <label>Госномер дополнительно (необязательно)<input class="in" name="plate" maxlength="20" autocomplete="off"></label>
          <label>Режим<select class="in" name="mode"><option value="live">Реальные источники</option><option value="demo">Демо · синтетическая история</option></select></label>
        </div>
        <p class="analysis-warning" data-demo-note hidden>ДЕМО: история полностью вымышлена. Её нельзя использовать для решения о покупке реального автомобиля.</p>
        <button class="btn btn-ghost" type="button" data-demo-vin hidden>Вставить учебный VIN</button>
        <div data-live-consents>
          <label class="vehicle-check-consent"><input type="checkbox" name="consent_to_external">Разрешаю передать VIN/госномер сервису ШАССИ и источникам проверки.</label>
          <label class="vehicle-check-consent" data-paid-label hidden><input type="checkbox" name="allow_paid">Разрешаю платные источники для этой проверки. Стоимость и баланс определяет агрегатор; денежный лимит задаётся в его кабинете.</label>
          <p class="muted" data-paid-note>Платные источники выключены в настройках основного сервиса.</p>
        </div>
        <button class="btn btn-primary" type="submit" data-start-check disabled>Проверить историю</button>
      </form>
      <p data-check-message role="status" aria-live="polite"></p>
      <div data-check-report></div>
    </section>`;
  }

  function reportHTML(check) {
    const report = check?.report;
    if (!report) return check ? `<p class="analysis-warning">${esc(check.message || "Отчёт пока недоступен")}</p>` : "";
    const summary = report.summary;
    return `${report.demo ? '<p class="analysis-warning"><strong>СИНТЕТИЧЕСКИЙ ДЕМО-ОТЧЁТ</strong> · события и оценка вымышлены.</p>' : ""}
      ${check.identity_changed ? '<p class="analysis-warning">VIN лота изменился после проверки. Этот отчёт относится к прежнему автомобилю.</p>' : ""}
      ${check.identity_warning ? `<p class="analysis-warning">${esc(check.identity_warning)}</p>` : ""}
      <p class="muted">${report.demo ? "Демо" : "Реальные источники"} · получен ${esc(date(report.created_at))} · срок до ${esc(date(check.expires_at))}</p>
      <p><b>${esc(report.vehicle.make)} ${esc(report.vehicle.model)} ${esc(report.vehicle.year || "")}</b> · VIN ${esc(report.vehicle.vin)}</p>
      <h4>${esc(summary.verdict)}</h4><p>${esc(summary.headline)}</p>
      <p>Индекс истории: <b>${summary.score == null ? "—" : esc(summary.score) + "/100"}</b> · риск: ${esc(levels[summary.level] || summary.level)}.</p>
      <p>Доступно блоков: ${esc(report.coverage.available)}/${esc(report.coverage.total)}. Индекс не является вероятностью победы на торгах.</p>
      ${summary.factors.length ? `<h4>Факторы риска</h4><ul>${summary.factors.map(f=>`<li><b>${esc(f.title)}</b>: ${esc(f.detail)} <span class="muted">[${esc(f.source)}]</span></li>`).join("")}</ul>` : ""}
      <div class="vehicle-check-facts">${report.facts.map(f=>`<div><span>${esc(f.title)}</span><b>${f.value == null ? "неизвестно" : esc(f.value)}</b><small>${esc(labels[f.status])}</small></div>`).join("")}</div>
      <details open><summary>Покрытие проверки и неизвестные сведения</summary>
        <div class="vehicle-check-table"><table><thead><tr><th>Блок</th><th>Состояние</th><th>Ограничения</th></tr></thead><tbody>${report.coverage.blocks.map(b=>`<tr><td>${esc(b.title)}</td><td>${esc(labels[b.status])}</td><td>${esc(b.note || "—")}</td></tr>`).join("")}</tbody></table></div>
      </details>
      <details><summary>Источники и время проверки</summary><ul>${report.sources.map(s=>`<li>${esc(s.name)}${s.via ? " · через "+esc(s.via) : ""}: ${esc(labels[s.status])} · ${esc(date(s.fetched_at))}${s.note ? " · "+esc(s.note) : ""}</li>`).join("")}</ul></details>
      <ul class="muted">${report.limitations.map(text=>`<li>${esc(text)}</li>`).join("")}</ul>
      <p class="muted">Отчёт не меняет исходные сведения лота и не передаётся DeepSeek автоматически. Перед расчётом выгоды проверьте риски и расходы.</p>`;
  }

  function mount(root, lot, api) {
    dispose();
    if (!root) return;
    let active = true, configured = false, pending = false, status = null, previousPayload = "", requestId = "";
    const form = root.querySelector("form");
    const field = name => form.elements.namedItem(name);
    const message = root.querySelector("[data-check-message]");
    const button = root.querySelector("[data-start-check]");
    const output = root.querySelector("[data-check-report]");
    const sync = () => {
      const demo = field("mode").value === "demo";
      for (const input of form.querySelectorAll("input,select")) input.disabled = pending;
      root.querySelector("[data-demo-note]").hidden = !demo;
      root.querySelector("[data-demo-vin]").hidden = !demo;
      root.querySelector("[data-live-consents]").hidden = demo;
      root.querySelector("[data-paid-label]").hidden = !status?.allow_paid;
      root.querySelector("[data-paid-note]").hidden = Boolean(status?.allow_paid);
      button.disabled = pending || !configured || (!demo && !field("consent_to_external").checked);
      root.querySelector("[data-demo-vin]").disabled = pending;
    };
    field("mode").onchange = () => {
      field("consent_to_external").checked = false;
      field("allow_paid").checked = false;
      message.textContent = "";
      // Do not leave a demo report presented under the live-mode selector.
      output.innerHTML = "";
      sync();
    };
    field("consent_to_external").onchange = sync;
    root.querySelector("[data-demo-vin]").onclick = () => { field("query").value = lot.vin || "XTA219050M0123456"; };
    form.onsubmit = async event => {
      event.preventDefault();
      if (pending || !configured) return;
      const mode = field("mode").value;
      if (mode === "live" && !field("consent_to_external").checked) { message.textContent = "Сначала подтвердите передачу данных"; return; }
      const payload = {query:field("query").value,plate:field("plate").value,mode,
        consent_to_external:mode === "live" && field("consent_to_external").checked,
        allow_paid:mode === "live" && Boolean(status?.allow_paid) && field("allow_paid").checked};
      const fingerprint = JSON.stringify(payload);
      if (fingerprint !== previousPayload || !requestId) {
        requestId = crypto.randomUUID(); previousPayload = fingerprint;
      }
      pending = true; sync(); message.textContent = "Проверка выполняется… Закрытие карточки не отменяет запрос на сервере.";
      try {
        const result = await api(`/api/lots/${lot.id}/vehicle-check`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...payload,request_id:requestId})});
        if (!active) return;
        output.innerHTML = reportHTML(result.check);
        message.textContent = result.check.status === "done" ? (result.reuse === "new" ? "Проверка завершена" : "Сохранённый результат · новый платный запрос не выполнялся") : result.check.message;
      } catch (error) {
        if (active) message.textContent = error.message;
      } finally {
        if (active) { pending = false; sync(); }
      }
    };
    const ready = async () => {
      try {
        const [config, history] = await Promise.all([api("/api/vehicle-checks/status"),api(`/api/lots/${lot.id}/vehicle-check`)]);
        if (!active) return;
        status = config; configured = config.configured;
        root.querySelector("[data-check-status]").textContent = config.message;
        field("mode").value = config.default_mode;
        if (config.demo_only) field("mode").querySelector('option[value="live"]').disabled = true;
        output.innerHTML = reportHTML(history.check);
        if (history.check?.report && history.check.mode !== config.default_mode) {
          message.textContent = "Ниже сохранён предыдущий отчёт в режиме «" + (history.check.mode === "demo" ? "демо" : "реальные источники") + "»";
        }
        sync();
      } catch (error) {
        if (active) { root.querySelector("[data-check-status]").textContent = error.message; sync(); }
      }
    };
    dispose = () => { active = false; form.onsubmit = null; };
    ready();
  }
  return {shell,mount,unmount:()=>dispose()};
})();
