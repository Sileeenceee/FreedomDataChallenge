import { api } from "../api.js";
import { go, trackBatch } from "../app.js";
import { dt, field, h, modal, num, pageHeader, select, toast, setChildren } from "../ui.js";

// ---------------------------------------------------------------- list
export async function renderChecklists(root) {
  const list = await api.checklists();
  const families = new Map();
  list.forEach((c) => {
    if (!families.has(c.family)) families.set(c.family, []);
    families.get(c.family).push(c);
  });

  setChildren(
    root,
    pageHeader({
      title: "Чек-листы",
      subtitle: "Чтобы заменить критерий: «Редактировать» → измените → «Сохранить версию» → «Применить к диалогам».",
      actions: [h("button", { class: "btn", onClick: () => importDialog(null) }, "Импорт JSON"), h("a", { class: "btn btn-primary", href: "#/checklists/new" }, "Новый чек-лист")],
    }),
    [...families.values()].map((versions) => {
      const latest = versions[0];
      return h(
        "section",
        { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", {}, latest.name), h("span", { class: "muted small" }, `${versions.length} верс.`)),
        h(
          "table",
          { class: "table" },
          h("thead", {}, h("tr", {}, ["Версия", "Критериев", "Комментарий", "Создана", "", ""].map((t) => h("th", {}, t)))),
          h(
            "tbody",
            {},
            versions.map((c) =>
              h(
                "tr",
                {},
                h("td", {}, h("b", {}, `v${c.version}`), c.is_active ? h("span", { class: "badge badge-good" }, "активный") : null),
                h("td", {}, c.criteria_count),
                h("td", {}, h("div", { class: "preview" }, c.comment || "—")),
                h("td", { class: "small muted" }, dt(c.created_at)),
                h(
                  "td",
                  { class: "row-actions" },
                  h("a", { class: "btn btn-sm", href: `#/checklists/${c.id}` }, c === latest ? "Редактировать" : "Открыть"),
                  !c.is_active
                    ? h("button", { class: "btn btn-sm", onClick: async () => { await api.activateChecklist(c.id); toast(`Активна v${c.version}`, "ok"); renderChecklists(root); } }, "Сделать активным")
                    : null,
                  h("button", { class: "btn btn-sm", onClick: () => applyDialog(c) }, "Применить к диалогам"),
                  h("button", { class: "btn btn-sm", onClick: async () => download(await api.checklist(c.id)) }, "JSON"),
                ),
                h("td", {}),
              ),
            ),
          ),
        ),
      );
    }),
  );
}

export async function applyDialog(checklist) {
  const convs = await api.conversations();
  const groups = [...new Set(convs.map((c) => c.group))].sort();
  const scope = select([...groups.map((g) => [`group:${g}`, `Группа ${g} (${convs.filter((c) => c.group === g).length})`]), ["all", `Все диалоги (${convs.length})`]], groups.length ? `group:${groups[groups.length - 1]}` : "all");
  const onlyMissing = h("input", { type: "checkbox", checked: true });
  modal({
    title: `Применить «${checklist.name}» v${checklist.version}`,
    body: h(
      "div",
      { class: "stack" },
      field("Диалоги", scope),
      h("label", { class: "check" }, onlyMissing, " Пропустить диалоги, уже оценённые этой версией"),
      h("p", { class: "muted small" }, "Каждый диалог получит новую оценку по этой версии чек-листа. Старые оценки останутся в истории."),
    ),
    actions: [
      {
        label: "Запустить оценку",
        kind: "primary",
        onClick: async () => {
          const body = { checklist_id: checklist.id, only_missing: onlyMissing.checked };
          if (scope.value.startsWith("group:")) body.group = scope.value.slice(6);
          const r = await api.evaluate(body);
          if (!r.count) return toast("Нечего оценивать", "warn");
          trackBatch(r.batch_id);
          toast(`В очереди: ${r.count}`, "ok");
          go("#/dialogs");
        },
      },
    ],
  });
}

function download(checklist) {
  const blob = new Blob([JSON.stringify(checklist.config, null, 2)], { type: "application/json" });
  const a = h("a", { href: URL.createObjectURL(blob), download: `checklist-v${checklist.version}.json` });
  a.click();
  URL.revokeObjectURL(a.href);
}

function importDialog(baseId, onLoaded) {
  const file = h("input", { type: "file", accept: ".json,application/json", class: "input" });
  const text = h("textarea", { class: "input mono", rows: 10, placeholder: '{"name": "...", "sections": [...], "criteria": [...], "scoring": {...}}' });
  modal({
    title: "Импорт чек-листа (JSON)",
    wide: true,
    body: h("div", { class: "stack" }, field("Файл", file), field("…или вставьте JSON", text)),
    actions: [
      {
        label: onLoaded ? "Загрузить в редактор" : "Сохранить как новый чек-лист",
        kind: "primary",
        onClick: async () => {
          const raw = file.files[0] ? await file.files[0].text() : text.value;
          const config = JSON.parse(raw);
          if (onLoaded) return onLoaded(config);
          const saved = await api.saveChecklist({ config, base_id: baseId, comment: "Импорт JSON", activate: true });
          toast(`Сохранено: ${saved.name} v${saved.version}`, "ok");
          go(`#/checklists/${saved.id}`);
        },
      },
    ],
  });
}

// ---------------------------------------------------------------- editor
const blankCriterion = (n, sectionId) => ({
  id: `crit_${n}`,
  section_id: sectionId || null,
  title: "Новый критерий",
  description: "",
  type: "binary",
  scale: { min: 0, max: 1, labels: {} },
  weight: 10,
  na_allowed: false,
  na_rule: "",
  critical: false,
  critical_rule: "",
});

export async function renderChecklistEditor(root, _params, id) {
  const isNew = id === "new";
  const source = isNew ? null : await api.checklist(Number(id));
  const cfg = structuredClone(
    source?.config || {
      name: "Новый чек-лист",
      description: "",
      sections: [{ id: "main", title: "Основное" }],
      criteria: [blankCriterion(1, "main")],
      scoring: { max_total: 100, decimals: 0, rounding: "half_up", critical_policy: "flag" },
    },
  );
  cfg.criteria.forEach((c) => (c.scale = c.scale || { min: 0, max: 1, labels: {} }));
  let dirty = false;
  const markDirty = () => {
    dirty = true;
    weightInfo();
  };

  const weightBox = h("span", { class: "weight-total" });
  function weightInfo() {
    const total = cfg.criteria.reduce((s, c) => s + (Number(c.weight) || 0), 0);
    weightBox.textContent = `Сумма весов: ${num(total, 2)}${total !== cfg.scoring.max_total ? ` (нормируется к ${cfg.scoring.max_total})` : ""}`;
  }

  // inputs bound to an object key
  const bind = (obj, key, props = {}, cast = (v) => v) =>
    h(props.multiline ? "textarea" : "input", {
      class: "input",
      value: obj[key] ?? "",
      rows: props.rows,
      type: props.type,
      min: props.min,
      step: props.step,
      placeholder: props.placeholder,
      onInput: (e) => {
        obj[key] = cast(e.target.value);
        markDirty();
        props.onInput?.(e);
      },
    });
  const toNum = (v) => (v === "" ? 0 : Number(v));

  // ---- general + scoring
  const general = h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, "Общие настройки")),
    h(
      "div",
      { class: "form-grid" },
      field("Название", bind(cfg, "name")),
      field("Описание", bind(cfg, "description", { multiline: true, rows: 2 })),
      field("Максимум баллов", bind(cfg.scoring, "max_total", { type: "number", min: 1 }, toNum)),
      field("Знаков после запятой", bind(cfg.scoring, "decimals", { type: "number", min: 0 }, toNum)),
      field(
        "Округление итога",
        select(
          [
            ["half_up", "Математическое (0.5 вверх)"],
            ["half_even", "Банковское"],
            ["floor", "Вниз"],
            ["ceil", "Вверх"],
          ],
          cfg.scoring.rounding,
          { onChange: (e) => { cfg.scoring.rounding = e.target.value; markDirty(); } },
        ),
      ),
      field(
        "Грубая ошибка",
        select(
          [
            ["flag", "Отмечать отдельно (итог не меняется)"],
            ["zero_total", "Обнулять итог"],
          ],
          cfg.scoring.critical_policy,
          { onChange: (e) => { cfg.scoring.critical_policy = e.target.value; markDirty(); } },
        ),
      ),
    ),
  );

  // ---- sections
  const sectionsBox = h("section", { class: "panel" });
  function renderSections() {
    setChildren(sectionsBox, 
      h("div", { class: "panel-head" }, h("h2", {}, "Секции"), h("button", { class: "btn btn-sm", onClick: () => { cfg.sections.push({ id: `s${cfg.sections.length + 1}`, title: "Новая секция" }); markDirty(); renderSections(); renderCriteria(); } }, "+ Секция")),
      cfg.sections.length
        ? cfg.sections.map((s, i) =>
            h(
              "div",
              { class: "section-edit" },
              field("ID", bind(s, "id", { onInput: () => renderCriteria() })),
              field("Название", bind(s, "title", { onInput: () => renderCriteria() })),
              h("button", { class: "icon-btn", title: "Удалить секцию", onClick: () => { cfg.criteria.forEach((c) => c.section_id === s.id && (c.section_id = null)); cfg.sections.splice(i, 1); markDirty(); renderSections(); renderCriteria(); } }, "×"),
            ),
          )
        : h("p", { class: "muted small" }, "Без секций — итог считается по всем критериям."),
    );
  }

  // ---- criteria
  const criteriaBox = h("div", { class: "stack" });
  function renderCriteria() {
    setChildren(criteriaBox, ...cfg.criteria.map((c, i) => criterionEditor(c, i)));
    weightInfo();
  }

  function criterionEditor(c, i) {
    const scaleBox = h("div", { class: "scale-box" });
    function renderScale() {
      if (c.type === "binary") {
        c.scale = { min: 0, max: 1, labels: c.scale?.labels || {} };
      }
      const rows = [];
      for (let v = c.scale.min; v <= c.scale.max && v - c.scale.min <= 10; v++) {
        rows.push(
          h(
            "label",
            { class: "scale-label" },
            h("span", { class: "mono" }, v),
            h("input", {
              class: "input",
              value: c.scale.labels?.[String(v)] || "",
              placeholder: c.type === "binary" ? (v ? "Да" : "Нет") : "подпись уровня",
              onInput: (e) => {
                c.scale.labels = { ...(c.scale.labels || {}), [String(v)]: e.target.value };
                if (!e.target.value) delete c.scale.labels[String(v)];
                markDirty();
              },
            }),
          ),
        );
      }
      setChildren(scaleBox, 
        c.type === "scale"
          ? h(
              "div",
              { class: "row" },
              field("Мин.", bind(c.scale, "min", { type: "number", onInput: () => renderScale() }, toNum)),
              field("Макс.", bind(c.scale, "max", { type: "number", onInput: () => renderScale() }, toNum)),
            )
          : null,
        h("div", { class: "scale-labels" }, rows),
      );
    }
    renderScale();

    const naRule = field("Правило N/A", bind(c, "na_rule", { multiline: true, rows: 2, placeholder: "Когда критерий неприменим" }));
    naRule.hidden = !c.na_allowed;
    const critRule = field("Что считается грубой ошибкой", bind(c, "critical_rule", { multiline: true, rows: 2 }));
    critRule.hidden = !c.critical;

    const move = (d) => {
      const j = i + d;
      if (j < 0 || j >= cfg.criteria.length) return;
      [cfg.criteria[i], cfg.criteria[j]] = [cfg.criteria[j], cfg.criteria[i]];
      markDirty();
      renderCriteria();
    };

    const isOpen = !!c._open;
    return h(
      "div",
      { class: "criterion-edit" },
      h(
        "div",
        { class: "criterion-edit-head" },
        h("span", { class: "mono muted" }, `${i + 1}.`),
        h("span", { class: "strong" }, c.title || "(без названия)"),
        h("span", { class: "muted small" }, `вес ${c.weight}`),
        h(
          "span",
          { class: "summary-actions" },
          h("button", { class: "icon-btn", title: "Вверх", onClick: () => move(-1) }, "↑"),
          h("button", { class: "icon-btn", title: "Вниз", onClick: () => move(1) }, "↓"),
          h("button", { class: "icon-btn", title: "Дублировать", onClick: () => { cfg.criteria.splice(i + 1, 0, { ...structuredClone(c), id: `${c.id}_copy`, _open: true }); markDirty(); renderCriteria(); } }, "⧉"),
          h("button", { class: "icon-btn danger", title: "Удалить", onClick: () => { if (cfg.criteria.length === 1) return toast("Нужен хотя бы один критерий", "warn"); cfg.criteria.splice(i, 1); markDirty(); renderCriteria(); } }, "×"),
          h(
            "button",
            {
              class: "btn btn-sm btn-ghost edit-toggle",
              onClick: (e) => {
                const form = e.currentTarget.closest(".criterion-edit").querySelector(".form-grid");
                form.hidden = !form.hidden;
                c._open = !form.hidden;
                e.currentTarget.textContent = form.hidden ? "Изменить" : "Свернуть";
              },
            },
            isOpen ? "Свернуть" : "Изменить",
          ),
        ),
      ),
      h(
        "div",
        { class: "form-grid", hidden: !isOpen },
        field("Название", bind(c, "title")),
        field("ID (латиница)", bind(c, "id"), "Используется в API и ссылках; уникален в чек-листе"),
        field("Секция", select([["", "— без секции —"], ...cfg.sections.map((s) => [s.id, s.title])], c.section_id || "", { onChange: (e) => { c.section_id = e.target.value || null; markDirty(); } })),
        field("Вес", bind(c, "weight", { type: "number", min: 0, step: "any" }, toNum)),
        h("div", { class: "span-2" }, field("Что проверять (инструкция для оценщика)", bind(c, "description", { multiline: true, rows: 3, placeholder: "Конкретное условие выполнения и как выбирать балл" }))),
        field(
          "Тип ответа",
          select(
            [
              ["binary", "Да / Нет"],
              ["scale", "Числовая шкала"],
            ],
            c.type,
            {
              onChange: (e) => {
                c.type = e.target.value;
                if (c.type === "scale" && c.scale.max <= 1) c.scale = { min: 0, max: 2, labels: {} };
                markDirty();
                renderScale();
              },
            },
          ),
        ),
        h("div", {}, h("span", { class: "field-label" }, "Шкала и подписи"), scaleBox),
        h(
          "div",
          { class: "span-2 flags" },
          h("label", { class: "check" }, h("input", { type: "checkbox", checked: c.na_allowed, onChange: (e) => { c.na_allowed = e.target.checked; naRule.hidden = !c.na_allowed; markDirty(); } }), " Допускается N/A"),
          h("label", { class: "check" }, h("input", { type: "checkbox", checked: c.critical, onChange: (e) => { c.critical = e.target.checked; critRule.hidden = !c.critical; markDirty(); } }), " Критичный (грубая ошибка при минимальном балле)"),
        ),
        h("div", { class: "span-2" }, naRule),
        h("div", { class: "span-2" }, critRule),
      ),
    );
  }

  // ---- save
  const comment = h("input", { class: "input", placeholder: "Комментарий к версии (что изменено)" });
  const activate = h("input", { type: "checkbox", checked: true });

  async function save() {
    const config = structuredClone(cfg);
    config.criteria.forEach((c) => delete c._open);
    const saved = await api.saveChecklist({ config, base_id: source?.id ?? null, comment: comment.value.trim() || null, activate: activate.checked });
    dirty = false;
    toast(`Сохранено: ${saved.name} v${saved.version}`, "ok");
    modal({
      title: "Чек-лист сохранён",
      body: h("p", {}, `Версия v${saved.version}${saved.is_active ? " стала активной" : ""}. Применить её к диалогам сейчас?`),
      actions: [{ label: "Применить к диалогам…", kind: "primary", onClick: () => applyDialog(saved) }],
    });
    go(`#/checklists/${saved.id}`);
  }

  setChildren(
    root,
    pageHeader({
      back: { href: "#/checklists", label: "Чек-листы" },
      title: isNew ? "Новый чек-лист" : source.name,
      chips: isNew ? null : [h("span", { class: "chip" }, `v${source.version}`), source.is_active ? h("span", { class: "badge badge-good" }, "активный") : null],
      subtitle: "Измените критерии и нажмите «Сохранить версию» внизу.",
      actions: [
        h("button", { class: "btn", onClick: () => importDialog(source?.id ?? null, (imported) => { Object.keys(cfg).forEach((k) => delete cfg[k]); Object.assign(cfg, imported); cfg.scoring = cfg.scoring || { max_total: 100, decimals: 0, rounding: "half_up", critical_policy: "flag" }; cfg.sections = cfg.sections || []; cfg.criteria.forEach((c) => (c.scale = c.scale || { min: 0, max: 1, labels: {} })); markDirty(); renderSections(); renderCriteria(); toast("Загружено в редактор — проверьте и сохраните", "ok"); }) }, "Импорт JSON"),
        source ? h("button", { class: "btn", onClick: () => applyDialog(source) }, "Применить к диалогам") : null,
      ],
    }),
    general,
    sectionsBox,
    h(
      "section",
      { class: "panel" },
      h(
        "div",
        { class: "panel-head" },
        h("h2", {}, "Критерии"),
        weightBox,
        h("button", { class: "btn btn-sm", onClick: () => { cfg.criteria.push({ ...blankCriterion(cfg.criteria.length + 1, cfg.sections[0]?.id), _open: true }); markDirty(); renderCriteria(); } }, "+ Критерий"),
      ),
      h("p", { class: "small muted" }, "Баллы по шкале пересчитываются с учётом веса; при N/A вес перераспределяется между применимыми критериями. Новый критерий начинает применяться при следующей оценке — без изменения кода."),
      criteriaBox,
    ),
    h("div", { class: "save-bar" }, comment, h("label", { class: "check" }, activate, " Сделать активным"), h("button", { class: "btn btn-primary", onClick: () => save().catch((e) => toast(e.message, "error")) }, "Сохранить версию")),
  );
  renderSections();
  renderCriteria();

  const beforeUnload = (e) => {
    if (dirty) e.preventDefault();
  };
  window.addEventListener("beforeunload", beforeUnload);
  return () => window.removeEventListener("beforeunload", beforeUnload);
}
