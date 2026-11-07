import { api } from "../api.js";
import { refreshBadges, trackBatch } from "../app.js";
import { CHANNEL, STATUS, emptyState, h, modal, money, num, scorePill, select, field, toast, setChildren } from "../ui.js";

const filters = { q: "", group: "", language: "", channel: "", status: "" };
const selected = new Set();

export async function renderConversations(root) {
  const [list, stats, checklists] = await Promise.all([api.conversations(), api.stats(), api.checklists()]);
  let conversations = list;

  const groups = [...new Set(conversations.map((c) => c.group))].sort();
  const activeChecklist = checklists.find((c) => c.is_active);

  const statsRow = h("div", { class: "stats" });
  const tableWrap = h("div", { class: "card table-card" });
  const countLabel = h("span", { class: "muted" });
  const selectionBar = h("div", { class: "selection-bar" });

  function renderStats(s) {
    setChildren(statsRow, 
      stat("Диалогов", num(s.conversations, 0), groups.length ? `групп: ${groups.join(", ")}` : null),
      stat("Оценено", num(s.evaluated, 0), s.in_progress ? `в работе: ${s.in_progress}` : s.errors ? `ошибок: ${s.errors}` : null),
      stat("Средний балл", num(s.avg_total, 1), Object.entries(s.by_language || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || null),
      stat("Грубые ошибки", num(s.critical, 0), s.evaluated ? `${num((100 * s.critical) / s.evaluated, 0)}% диалогов` : null, s.critical ? "bad" : null),
      stat("Апелляции", num(s.appeals_open, 0), `решено: ${s.appeals_decided}`, s.appeals_open ? "warn" : null),
      stat("Стоимость оценки", money(s.avg_cost_usd), "в среднем за диалог"),
    );
  }

  function matches(c) {
    const ev = c.evaluation;
    if (filters.q && !`${c.id} ${c.preview}`.toLowerCase().includes(filters.q.toLowerCase())) return false;
    if (filters.group && c.group !== filters.group) return false;
    if (filters.language && c.language !== filters.language) return false;
    if (filters.channel && c.channel !== filters.channel) return false;
    if (filters.status === "none" && ev) return false;
    if (filters.status === "done" && ev?.status !== "done") return false;
    if (filters.status === "critical" && !ev?.critical_error) return false;
    if (filters.status === "appeal" && !ev?.appeals_open) return false;
    if (filters.status === "review" && !ev?.low_confidence) return false;
    if (filters.status === "error" && ev?.status !== "error") return false;
    return true;
  }

  function renderTable() {
    const rows = conversations.filter(matches);
    countLabel.textContent = `Показано ${rows.length} из ${conversations.length}`;
    renderSelectionBar();
    if (!conversations.length) {
      setChildren(tableWrap, 
        emptyState("Диалогов пока нет", "Загрузите набор из API организаторов или JSON-файл с транскриптами.", h("button", { class: "btn btn-primary", onClick: importDialog }, "Импорт из API")),
      );
      return;
    }
    const allChecked = rows.length > 0 && rows.every((r) => selected.has(r.id));
    setChildren(tableWrap, 
      h(
        "table",
        { class: "table" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            h("th", { class: "col-check" }, h("input", { type: "checkbox", checked: allChecked, onChange: (e) => { rows.forEach((r) => (e.target.checked ? selected.add(r.id) : selected.delete(r.id))); renderTable(); }, "aria-label": "Выбрать все" })),
            h("th", {}, "Диалог"),
            h("th", {}, "Канал"),
            h("th", {}, "Язык"),
            h("th", { class: "num" }, "Реплик"),
            h("th", {}, "Обращение клиента"),
            h("th", { class: "num" }, "Итог"),
            h("th", {}, "Статус"),
          ),
        ),
        h(
          "tbody",
          {},
          rows.map((c) => {
            const ev = c.evaluation;
            return h(
              "tr",
              { class: selected.has(c.id) ? "row-selected" : "", onClick: (e) => { if (e.target.closest("input,a,button")) return; location.hash = `#/c/${encodeURIComponent(c.id)}`; } },
              h("td", { class: "col-check" }, h("input", { type: "checkbox", checked: selected.has(c.id), onChange: (e) => { e.target.checked ? selected.add(c.id) : selected.delete(c.id); renderTable(); } })),
              h("td", {}, h("a", { href: `#/c/${encodeURIComponent(c.id)}`, class: "mono strong" }, c.id), h("div", { class: "muted small" }, c.group)),
              h("td", {}, h("span", { class: `chip chip-${c.channel === "voice call" ? "call" : "chat"}` }, c.channel === "voice call" ? "☎ " : "💬 ", CHANNEL[c.channel] || c.channel)),
              h("td", {}, h("span", { class: "chip" }, c.language)),
              h("td", { class: "num" }, c.turns, h("div", { class: "muted small" }, `опер.: ${c.operator_turns}`)),
              h("td", {}, h("div", { class: "preview" }, c.preview)),
              h("td", { class: "num" }, ev?.status === "done" ? scorePill(ev.total, ev.max_total) : "—", ev && ev.original_total !== null && ev.original_total !== ev.total ? h("div", { class: "muted small" }, `было ${num(ev.original_total, 2)}`) : null),
              h("td", {}, statusCell(ev)),
            );
          }),
        ),
      ),
    );
  }

  function renderSelectionBar() {
    setChildren(selectionBar, 
      selected.size
        ? [
            h("span", {}, `Выбрано: ${selected.size}`),
            h("button", { class: "btn btn-primary btn-sm", onClick: () => evaluateDialog([...selected]) }, "Оценить выбранные"),
            h("button", { class: "btn btn-sm", onClick: () => { selected.clear(); renderTable(); } }, "Снять выбор"),
          ]
        : [],
    );
    selectionBar.hidden = !selected.size;
  }

  function importDialog() {
    const group = h("input", { class: "input", value: "DEV", placeholder: "group_id" });
    modal({
      title: "Импорт из API организаторов",
      body: h(
        "div",
        { class: "stack" },
        field("group_id", group, "DEV — набор для разработки. Для контрольного набора введите значение, которое сообщат организаторы."),
        h("p", { class: "muted small" }, "Запрос выполняется сервером (без CORS). Повторный импорт обновляет транскрипты, оценки сохраняются."),
      ),
      actions: [
        {
          label: "Импортировать",
          kind: "primary",
          onClick: async () => {
            const r = await api.importGroup(group.value.trim() || "DEV");
            toast(`Группа ${r.group}: новых ${r.created}, обновлено ${r.updated}`, "ok");
            await reload();
          },
        },
      ],
    });
  }

  function uploadDialog() {
    const file = h("input", { type: "file", accept: ".json,application/json", class: "input" });
    const group = h("input", { class: "input", placeholder: "например, UPLOAD (необязательно)" });
    modal({
      title: "Загрузить транскрипты (JSON)",
      body: h(
        "div",
        { class: "stack" },
        field("Файл", file, "Формат API организаторов, {records:[…]}, массив записей или одна запись с conversation_id и transcript."),
        field("Группа", group),
      ),
      actions: [
        {
          label: "Загрузить",
          kind: "primary",
          onClick: async () => {
            if (!file.files[0]) throw new Error("Выберите файл");
            const r = await api.upload(file.files[0], group.value.trim());
            toast(`Загружено: новых ${r.created}, обновлено ${r.updated}`, "ok");
            await reload();
          },
        },
      ],
    });
  }

  function evaluateDialog(ids) {
    const options = checklists.map((c) => [c.id, `${c.name} · v${c.version}${c.is_active ? " (активный)" : ""}`]);
    const cl = select(options, activeChecklist?.id);
    const scope = select(
      [
        ["selected", `Выбранные диалоги (${ids?.length || 0})`],
        ...groups.map((g) => [`group:${g}`, `Вся группа ${g}`]),
        ["all", "Все диалоги"],
      ],
      ids?.length ? "selected" : groups[0] ? `group:${groups[0]}` : "all",
    );
    const onlyMissing = h("input", { type: "checkbox" });
    modal({
      title: "Запустить оценку",
      body: h(
        "div",
        { class: "stack" },
        field("Чек-лист", cl),
        field("Что оценить", scope),
        h("label", { class: "check" }, onlyMissing, " Только диалоги без оценки по этому чек-листу"),
        h("p", { class: "muted small" }, "Оценка выполняется в фоне; прогресс — в правом верхнем углу. Предыдущие оценки сохраняются в истории карточки."),
      ),
      actions: [
        {
          label: "Оценить",
          kind: "primary",
          onClick: async () => {
            const body = { checklist_id: Number(cl.value), only_missing: onlyMissing.checked };
            if (scope.value === "selected") body.conversation_ids = ids;
            else if (scope.value.startsWith("group:")) body.group = scope.value.slice(6);
            const r = await api.evaluate(body);
            if (!r.count) return toast("Нечего оценивать: все диалоги уже оценены", "warn");
            toast(`В очереди: ${r.count}`, "ok");
            trackBatch(r.batch_id);
            selected.clear();
            await reload();
          },
        },
      ],
    });
  }

  async function reload() {
    const [l, s] = await Promise.all([api.conversations(), api.stats()]);
    conversations = l;
    renderStats(s);
    renderTable();
    refreshBadges();
  }

  const search = h("input", { class: "input search", placeholder: "Поиск по ID или тексту…", value: filters.q, onInput: (e) => { filters.q = e.target.value; renderTable(); } });
  const filterSel = (key, options) => select([["", options[0]], ...options.slice(1)], filters[key], { onChange: (e) => { filters[key] = e.target.value; renderTable(); } });

  setChildren(root, 
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", {}, "Диалоги"), h("p", { class: "muted" }, "Загрузите транскрипты, выберите чек-лист и запустите оценку работы оператора.")),
      h(
        "div",
        { class: "actions" },
        h("button", { class: "btn", onClick: uploadDialog }, "Загрузить JSON"),
        h("button", { class: "btn", onClick: importDialog }, "Импорт из API"),
        h("button", { class: "btn btn-primary", onClick: () => evaluateDialog([...selected]) }, "Оценить…"),
      ),
    ),
    statsRow,
    h(
      "div",
      { class: "toolbar" },
      search,
      filterSel("group", ["Все группы", ...groups.map((g) => [g, g])]),
      filterSel("language", ["Все языки", ["RU", "RU"], ["KZ", "KZ"], ["EN", "EN"]]),
      filterSel("channel", ["Все каналы", ["agent chat", "Чат"], ["voice call", "Звонок"]]),
      filterSel("status", ["Любой статус", ["none", "Без оценки"], ["done", "Оценены"], ["critical", "Грубая ошибка"], ["review", "Нужна проверка"], ["appeal", "Есть апелляция"], ["error", "Ошибка оценки"]]),
      countLabel,
    ),
    selectionBar,
    tableWrap,
  );
  renderStats(stats);
  renderTable();

  // Live refresh while evaluations are running.
  let t = null;
  const onProgress = () => {
    clearTimeout(t);
    t = setTimeout(reload, 400);
  };
  window.addEventListener("cqa:batch-progress", onProgress);
  window.addEventListener("cqa:batch-finished", onProgress);
  const anyRunning = conversations.some((c) => ["pending", "running"].includes(c.evaluation?.status));
  const poll = anyRunning ? setInterval(reload, 3000) : null;
  return () => {
    window.removeEventListener("cqa:batch-progress", onProgress);
    window.removeEventListener("cqa:batch-finished", onProgress);
    clearTimeout(t);
    if (poll) clearInterval(poll);
  };
}

function stat(label, value, hint, tone) {
  return h("div", { class: `stat ${tone ? `stat-${tone}` : ""}` }, h("div", { class: "stat-label" }, label), h("div", { class: "stat-value" }, value), hint ? h("div", { class: "stat-hint" }, hint) : null);
}

function statusCell(ev) {
  if (!ev) return h("span", { class: "muted" }, "Не оценён");
  const parts = [h("span", { class: `status status-${ev.status}` }, STATUS[ev.status] || ev.status)];
  if (ev.critical_error) parts.push(h("span", { class: "badge badge-bad", title: "Грубая ошибка" }, "Грубая ошибка"));
  if (ev.appeals_open) parts.push(h("span", { class: "badge badge-warn" }, `Апелляция: ${ev.appeals_open}`));
  if (ev.low_confidence) parts.push(h("span", { class: "badge", title: "Критерии с низкой уверенностью модели" }, `Проверить: ${ev.low_confidence}`));
  if (ev.evaluator === "mock") parts.push(h("span", { class: "badge badge-mock" }, "MOCK"));
  return h("div", { class: "status-cell" }, parts);
}
