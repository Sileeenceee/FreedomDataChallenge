import { api } from "../api.js";
import { refreshBadges } from "../app.js";
import { APPEAL_STATUS, SPEAKER, answerLabel, dt, emptyState, field, h, num, scorePill, toast, turnTime, setChildren } from "../ui.js";

let tab = "open";

export async function renderAppeals(root) {
  const list = await api.appeals(tab);
  const tabs = h(
    "div",
    { class: "tabs" },
    [
      ["open", "На рассмотрении"],
      ["decided", "Решённые"],
      ["all", "Все"],
    ].map(([k, label]) =>
      h(
        "button",
        {
          class: `tab ${tab === k ? "active" : ""}`,
          onClick: () => {
            tab = k;
            renderAppeals(root);
          },
        },
        label,
      ),
    ),
  );

  const table = list.length
    ? h(
        "div",
        { class: "card table-card" },
        h(
          "table",
          { class: "table" },
          h("thead", {}, h("tr", {}, ["№", "Диалог", "Критерий", "Оценка ИИ", "Комментарий", "Статус", "Итог", "Подана"].map((t) => h("th", {}, t)))),
          h(
            "tbody",
            {},
            list.map((a) =>
              h(
                "tr",
                { onClick: (e) => !e.target.closest("a") && (location.hash = `#/appeals/${a.id}`) },
                h("td", {}, h("a", { href: `#/appeals/${a.id}`, class: "strong" }, `#${a.id}`)),
                h("td", {}, h("a", { href: `#/c/${encodeURIComponent(a.conversation_id)}?eval=${a.evaluation_id}`, class: "mono" }, a.conversation_id)),
                h("td", {}, a.criterion_title),
                h("td", {}, a.original_item?.applicable ? `${a.original_item.value}` : "N/A"),
                h("td", {}, h("div", { class: "preview" }, a.comment), a.author ? h("div", { class: "muted small" }, `— ${a.author}`) : null),
                h("td", {}, h("span", { class: `badge appeal-badge appeal-${a.status}` }, APPEAL_STATUS[a.status])),
                h("td", { class: "num" }, a.status === "open" ? scorePill(a.current_total, 100) : h("span", {}, num(a.total_before, 2), " → ", h("b", {}, num(a.total_after, 2)))),
                h("td", { class: "small muted" }, dt(a.created_at)),
              ),
            ),
          ),
        ),
      )
    : emptyState(tab === "open" ? "Нет апелляций на рассмотрении" : "Апелляций пока нет", "Апелляцию подают из карточки диалога кнопкой «Оспорить» у критерия.");

  setChildren(root, 
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Апелляции"), h("p", { class: "muted" }, "Проверяющий изучает спорную оценку, доказательства и комментарий, затем подтверждает или меняет оценку."))),
    tabs,
    table,
  );
}

export async function renderAppeal(root, _params, id) {
  const a = await api.appeal(Number(id));
  const c = a.criterion;
  const ev = a.evaluation;
  const original = a.original_item;
  const finalItem = ev.final_items.find((i) => i.criterion_id === a.criterion_id);
  const evidenceTurns = new Set((original.evidence || []).map((e) => e.turn));
  const range = original.checked_range;

  const fragment = h(
    "div",
    { class: "transcript fragment" },
    a.fragment.map((t) =>
      h(
        "div",
        { class: `turn turn-${t.speaker} ${evidenceTurns.has(t.idx) ? "hl-evidence" : range && t.idx >= range[0] && t.idx <= range[1] ? "hl-range" : ""}` },
        h(
          "div",
          { class: "turn-meta" },
          h("a", { class: "turn-idx", href: `#/c/${encodeURIComponent(a.conversation_id)}?eval=${ev.id}&turn=${t.idx}` }, `#${t.idx}`),
          h("span", { class: "turn-speaker" }, SPEAKER[t.speaker]),
          h("span", { class: "turn-time" }, turnTime(t)),
        ),
        h("div", { class: "turn-text" }, t.text),
      ),
    ),
  );

  const criterionBox = h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, c.title), c.critical ? h("span", { class: "badge badge-bad-outline" }, "критичный") : null),
    h("p", {}, c.description),
    h(
      "dl",
      { class: "kv small" },
      h("dt", {}, "Тип"),
      h("dd", {}, c.type === "binary" ? "Да/Нет" : `Шкала ${c.scale.min}–${c.scale.max}${Object.keys(c.scale.labels || {}).length ? `: ${Object.entries(c.scale.labels).map(([k, v]) => `${k} — ${v}`).join("; ")}` : ""}`),
      h("dt", {}, "Вес"),
      h("dd", {}, num(c.weight, 2)),
      h("dt", {}, "N/A"),
      h("dd", {}, c.na_allowed ? `допускается: ${c.na_rule || "—"}` : "не допускается"),
      c.critical ? [h("dt", {}, "Грубая ошибка"), h("dd", {}, c.critical_rule || "нарушение критерия")] : null,
    ),
    h("h3", {}, "Оценка ИИ (оспаривается)"),
    h("div", { class: "quote-box" }, h("div", {}, h("b", {}, answerLabel(c, original))), original.na_reason ? h("div", { class: "small" }, `N/A: ${original.na_reason}`) : null, h("p", {}, original.explanation)),
    (original.evidence || []).length
      ? h(
          "div",
          { class: "evidence-list" },
          original.evidence.map((e) => h("a", { class: "evidence", href: `#/c/${encodeURIComponent(a.conversation_id)}?eval=${ev.id}&turn=${e.turn}` }, h("span", { class: "evidence-idx" }, `#${e.turn}`), `«${e.quote}»`)),
        )
      : range
        ? h("div", { class: "checked-range" }, `Проверенный участок: #${range[0]}–#${range[1]}`)
        : null,
    h("h3", {}, "Комментарий апелляции"),
    h("div", { class: "quote-box appeal-comment" }, a.comment, h("div", { class: "small muted" }, `${a.author || "Без имени"} · ${dt(a.created_at)}`)),
  );

  const decisionBox = h("section", { class: "panel" });

  function renderDecided() {
    setChildren(decisionBox, 
      h("div", { class: "panel-head" }, h("h2", {}, "Решение проверяющего"), h("span", { class: `badge appeal-badge appeal-${a.status}` }, APPEAL_STATUS[a.status])),
      h(
        "dl",
        { class: "kv" },
        h("dt", {}, "Исходная оценка"),
        h("dd", {}, answerLabel(c, original)),
        h("dt", {}, "Решение"),
        h("dd", {}, a.status === "changed" ? `изменено на: ${answerLabel(c, { ...original, ...a.decided_item })}` : "оценка подтверждена"),
        h("dt", {}, "Причина"),
        h("dd", {}, a.decision_reason),
        h("dt", {}, "Проверяющий"),
        h("dd", {}, `${a.reviewer || "—"} · ${dt(a.decided_at)}`),
        h("dt", {}, "Итог диалога"),
        h("dd", {}, num(a.total_before, 2), " → ", h("b", {}, num(a.total_after, 2))),
        h("dt", {}, "Текущий итог"),
        h("dd", {}, scorePill(ev.final_result.total, ev.final_result.max_total), finalItem ? h("span", { class: "muted small" }, ` · критерий сейчас: ${answerLabel(c, finalItem)}`) : null),
      ),
      h("a", { class: "btn", href: `#/c/${encodeURIComponent(a.conversation_id)}?eval=${ev.id}` }, "Открыть карточку диалога"),
    );
  }

  function renderForm() {
    let decision = "confirm";
    const values = [];
    for (let v = c.scale.min; v <= c.scale.max; v++) values.push([String(v), `${v}${c.scale.labels?.[String(v)] ? ` — ${c.scale.labels[String(v)]}` : ""}`]);
    if (c.na_allowed) values.push(["na", "N/A — критерий неприменим"]);
    const current = original.applicable ? String(original.value) : "na";
    const valueSel = h(
      "select",
      { class: "input" },
      values.map(([v, l]) => h("option", { value: v, selected: v === current }, l)),
    );
    const valueField = field("Новая оценка", valueSel);
    valueField.hidden = true;
    const reason = h("textarea", { class: "input", rows: 3, placeholder: "Обоснование решения (обязательно)" });
    const reviewer = h("input", { class: "input", placeholder: "Имя проверяющего (необязательно)" });
    const radio = (val, label) =>
      h(
        "label",
        { class: "radio" },
        h("input", {
          type: "radio",
          name: "decision",
          value: val,
          checked: val === "confirm",
          onChange: () => {
            decision = val;
            valueField.hidden = val !== "change";
          },
        }),
        label,
      );
    setChildren(decisionBox, 
      h("div", { class: "panel-head" }, h("h2", {}, "Решение проверяющего"), h("span", { class: "badge appeal-badge appeal-open" }, APPEAL_STATUS.open)),
      h("div", { class: "stack" }, h("div", { class: "radio-group" }, radio("confirm", "Подтвердить оценку"), radio("change", "Изменить оценку")), valueField, field("Причина", reason), field("Проверяющий", reviewer)),
      h("p", { class: "small muted" }, `Текущий итог диалога: ${num(ev.final_result.total, 2)}. После изменения итог будет пересчитан с учётом весов и N/A.`),
      h(
        "button",
        {
          class: "btn btn-primary",
          onClick: async (e) => {
            if (reason.value.trim().length < 3) return toast("Укажите причину решения", "warn");
            e.currentTarget.disabled = true;
            try {
              const body = { decision, reason: reason.value.trim(), reviewer: reviewer.value.trim() || null };
              if (decision === "change") {
                body.applicable = valueSel.value !== "na";
                body.value = body.applicable ? Number(valueSel.value) : null;
              }
              const r = await api.decide(a.id, body);
              toast(`Решение сохранено. Итог: ${num(r.total_before, 2)} → ${num(r.total_after, 2)}`, "ok");
              refreshBadges();
              renderAppeal(root, null, id);
            } catch (err) {
              toast(err.message, "error");
              e.currentTarget.disabled = false;
            }
          },
        },
        "Сохранить решение",
      ),
    );
  }

  a.status === "open" ? renderForm() : renderDecided();

  setChildren(root, 
    h(
      "div",
      { class: "page-head" },
      h(
        "div",
        {},
        h("a", { href: "#/appeals", class: "back small" }, "← Апелляции"),
        h("h1", {}, `Апелляция #${a.id}`),
        h("div", { class: "chips" }, h("a", { class: "chip mono", href: `#/c/${encodeURIComponent(a.conversation_id)}?eval=${ev.id}` }, a.conversation_id), h("span", { class: "chip" }, a.language), h("span", { class: "chip" }, `оценка #${ev.id}`)),
      ),
    ),
    h(
      "div",
      { class: "card-grid" },
      h("div", { class: "stack" }, criterionBox, decisionBox),
      h(
        "section",
        { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", {}, "Фрагмент диалога"), h("a", { class: "small", href: `#/c/${encodeURIComponent(a.conversation_id)}?eval=${ev.id}${(original.evidence || [])[0] ? `&turn=${original.evidence[0].turn}` : ""}` }, "Открыть полностью →")),
        h("div", { class: "legend small" }, h("span", { class: "lg lg-evidence" }, "доказательство"), h("span", { class: "lg lg-range" }, "проверенный участок")),
        fragment,
      ),
    ),
  );
}
