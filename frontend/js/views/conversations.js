// Dialogues list: search, two filters, one table. Click a row to open the card.
import { evaluateDialog } from "../actions.js";
import { api } from "../api.js";
import { emptyState, h, num, pageHeader, scorePill, select, setChildren } from "../ui.js";

const filters = { q: "", language: "", status: "" };

export async function renderConversations(root) {
  let conversations = await api.conversations();
  const tableWrap = h("div", { class: "card table-card" });
  const countLabel = h("span", { class: "muted small" });

  function matches(c) {
    const ev = c.evaluation;
    if (filters.q && !`${c.id} ${c.preview}`.toLowerCase().includes(filters.q.toLowerCase())) return false;
    if (filters.language && c.language !== filters.language) return false;
    if (filters.status === "none" && ev?.status === "done") return false;
    if (filters.status === "critical" && !ev?.critical_error) return false;
    if (filters.status === "appeal" && !ev?.appeals_open) return false;
    return true;
  }

  function renderTable() {
    const rows = conversations.filter(matches);
    countLabel.textContent = `${rows.length} из ${conversations.length}`;
    if (!conversations.length) {
      setChildren(tableWrap, emptyState("Диалогов пока нет", "Загрузите их на странице «Обзор».", h("a", { class: "btn btn-primary", href: "#/" }, "К обзору")));
      return;
    }
    setChildren(
      tableWrap,
      h(
        "table",
        { class: "table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Диалог"), h("th", {}, "Обращение клиента"), h("th", { class: "num" }, "Итог"), h("th", {}, ""), h("th", {}, ""))),
        h(
          "tbody",
          {},
          rows.map((c) => {
            const ev = c.evaluation;
            const href = `#/c/${encodeURIComponent(c.id)}`;
            return h(
              "tr",
              {},
              h("td", { class: "cell-id" }, h("a", { href, class: "mono strong" }, c.id), h("span", { class: "muted small cell-lang" }, c.language, c.channel === "voice call" ? " · звонок" : "")),
              h("td", {}, h("div", { class: "preview" }, c.preview)),
              h("td", { class: "num" }, ev?.status === "done" ? scorePill(ev.total, ev.max_total) : h("span", { class: "muted" }, "—")),
              h("td", { class: "cell-status" }, statusBadge(ev)),
              h("td", { class: "cell-action" }, h("a", { class: "btn btn-sm btn-primary", href }, "Открыть")),
            );
          }),
        ),
      ),
    );
  }

  const reload = async () => {
    conversations = await api.conversations();
    renderTable();
  };

  setChildren(
    root,
    pageHeader({
      title: "Диалоги",
      actions: [h("button", { class: "btn btn-primary", onClick: () => evaluateDialog(reload) }, "Оценить…")],
    }),
    h(
      "div",
      { class: "toolbar" },
      h("input", { class: "input search", placeholder: "Поиск по ID или тексту", value: filters.q, onInput: (e) => { filters.q = e.target.value; renderTable(); } }),
      select([["", "Все языки"], "RU", "KZ", "EN"], filters.language, { onChange: (e) => { filters.language = e.target.value; renderTable(); } }),
      select(
        [
          ["", "Все"],
          ["none", "Без оценки"],
          ["critical", "Грубая ошибка"],
          ["appeal", "С апелляцией"],
        ],
        filters.status,
        { onChange: (e) => { filters.status = e.target.value; renderTable(); } },
      ),
      countLabel,
    ),
    tableWrap,
  );
  renderTable();

  // Refresh while evaluations run in the background.
  const onBatch = () => reload();
  window.addEventListener("cqa:batch-progress", onBatch);
  window.addEventListener("cqa:batch-finished", onBatch);
  return () => {
    window.removeEventListener("cqa:batch-progress", onBatch);
    window.removeEventListener("cqa:batch-finished", onBatch);
  };
}

/** One badge per row, most important first. */
function statusBadge(ev) {
  if (!ev) return h("span", { class: "badge" }, "Не оценён");
  if (ev.status === "pending" || ev.status === "running") return h("span", { class: "badge badge-warn" }, "Оценивается…");
  if (ev.status === "error") return h("span", { class: "badge badge-bad" }, "Ошибка");
  if (ev.appeals_open) return h("span", { class: "badge badge-warn" }, "Апелляция");
  if (ev.critical_error) return h("span", { class: "badge badge-bad" }, "Грубая ошибка");
  if (ev.original_total !== null && ev.original_total !== ev.total) return h("span", { class: "badge badge-info", title: `Было ${num(ev.original_total, 2)}` }, "Пересмотрено");
  return null;
}
