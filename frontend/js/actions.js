// Shared actions (modals) used by several pages: import, upload, run evaluation.
import { api } from "./api.js";
import { trackBatch } from "./app.js";
import { field, h, modal, select, toast } from "./ui.js";

export function importDialog(onDone) {
  const group = h("input", { class: "input", value: "DEV", placeholder: "group_id" });
  modal({
    title: "Импорт из API организаторов",
    body: h("div", { class: "stack" }, field("group_id", group, "DEV — набор для разработки. Для контрольного набора — значение от организаторов.")),
    actions: [
      {
        label: "Импортировать",
        kind: "primary",
        onClick: async () => {
          const r = await api.importGroup(group.value.trim() || "DEV");
          toast(`Группа ${r.group}: новых ${r.created}, обновлено ${r.updated}`, "ok");
          onDone?.();
        },
      },
    ],
  });
}

export function uploadDialog(onDone) {
  const file = h("input", { type: "file", accept: ".json,application/json", class: "input" });
  modal({
    title: "Загрузить JSON с диалогами",
    body: h("div", { class: "stack" }, field("Файл", file, "Формат как у API организаторов: записи с conversation_id и transcript.")),
    actions: [
      {
        label: "Загрузить",
        kind: "primary",
        onClick: async () => {
          if (!file.files[0]) throw new Error("Выберите файл");
          const r = await api.upload(file.files[0]);
          toast(`Загружено: новых ${r.created}, обновлено ${r.updated}`, "ok");
          onDone?.();
        },
      },
    ],
  });
}

export async function evaluateDialog(onDone) {
  const [checklists, convs] = await Promise.all([api.checklists(), api.conversations()]);
  const groups = [...new Set(convs.map((c) => c.group))].sort();
  const cl = select(
    checklists.map((c) => [c.id, `${c.name} · v${c.version}${c.is_active ? " (активный)" : ""}`]),
    checklists.find((c) => c.is_active)?.id,
  );
  const scope = select([...groups.map((g) => [`group:${g}`, `Группа ${g}`]), ["all", "Все диалоги"]], groups[0] ? `group:${groups[0]}` : "all");
  const onlyMissing = h("input", { type: "checkbox", checked: true });
  modal({
    title: "Запустить оценку",
    body: h(
      "div",
      { class: "stack" },
      field("Чек-лист", cl),
      field("Какие диалоги", scope),
      h("label", { class: "check" }, onlyMissing, " Пропустить уже оценённые этим чек-листом"),
    ),
    actions: [
      {
        label: "Оценить",
        kind: "primary",
        onClick: async () => {
          const body = { checklist_id: Number(cl.value), only_missing: onlyMissing.checked };
          if (scope.value.startsWith("group:")) body.group = scope.value.slice(6);
          const r = await api.evaluate(body);
          if (!r.count) return toast("Все диалоги уже оценены этим чек-листом", "warn");
          toast(`В очереди: ${r.count}`, "ok");
          trackBatch(r.batch_id);
          onDone?.();
        },
      },
    ],
  });
}
