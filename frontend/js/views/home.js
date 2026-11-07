// Start page: the four steps of the workflow and three key numbers. Nothing else.
import { evaluateDialog, importDialog, uploadDialog } from "../actions.js";
import { api } from "../api.js";
import { isMock } from "../app.js";
import { h, hero, num, setChildren, steps } from "../ui.js";

export async function renderHome(root) {
  const [s, checklists] = await Promise.all([api.stats(), api.checklists()]);
  const active = checklists.find((c) => c.is_active);
  const reload = () => renderHome(root);
  const n = s.conversations;

  setChildren(
    root,
    hero({
      eyebrow: "Freedom Broker · Conversation Quality Analytics",
      title: "Оценка качества диалогов",
    }),
    steps([
      {
        title: "Загрузите диалоги",
        text: n ? `Загружено: ${n}` : "Из API организаторов или JSON-файла",
        done: n > 0,
        actions: [h("button", { class: "btn btn-sm", onClick: () => importDialog(reload) }, "Импорт из API"), h("button", { class: "btn btn-sm btn-ghost", onClick: () => uploadDialog(reload) }, "JSON")],
      },
      {
        title: "Проверьте чек-лист",
        text: active ? `${active.name} · v${active.version}` : "Создайте чек-лист",
        done: !!active,
        actions: [h("a", { class: "btn btn-sm btn-ghost", href: active ? `#/checklists/${active.id}` : "#/checklists/new" }, "Открыть")],
      },
      {
        title: "Запустите оценку",
        text: n ? `Оценено: ${s.evaluated} из ${n}` : "Сначала загрузите диалоги",
        done: n > 0 && s.evaluated === n,
        current: n > 0 && s.evaluated < n,
        actions: [h("button", { class: "btn btn-sm btn-primary", disabled: !n, onClick: () => evaluateDialog(reload) }, "Оценить")],
      },
      {
        title: "Разберите результат",
        text: "Оценки, доказательства, апелляции",
        actions: [h("a", { class: "btn btn-sm btn-ghost", href: "#/dialogs" }, "К диалогам")],
      },
    ]),
    h(
      "div",
      { class: "stats stats-3" },
      tile("Оценено", `${num(s.evaluated, 0)} / ${num(n, 0)}`),
      tile("Средний балл", num(s.avg_total, 1)),
      tile("Открытые апелляции", num(s.appeals_open, 0), s.appeals_open ? "warn" : null),
    ),
    isMock() ? h("p", { class: "mock-line" }, "⚠ Демо-режим: модель не подключена, баллы условные.") : null,
  );

  const onBatch = () => reload();
  window.addEventListener("cqa:batch-finished", onBatch);
  return () => window.removeEventListener("cqa:batch-finished", onBatch);
}

function tile(label, value, tone) {
  return h("div", { class: `stat ${tone ? `stat-${tone}` : ""}` }, h("div", { class: "stat-label" }, label), h("div", { class: "stat-value" }, value));
}
