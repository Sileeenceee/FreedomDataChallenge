// Appeals: a simple queue, and a review page (dispute + decision on the left, dialogue fragment on the right).
import { api } from "../api.js";
import { refreshBadges } from "../app.js";
import { APPEAL_STATUS, answerLabel, chatMessage, dt, emptyState, field, h, label, num, pageHeader, scorePill, setChildren, toast } from "../ui.js";

let tab = "open";

export async function renderAppeals(root) {
  const list = await api.appeals(tab);
  const tabs = h(
    "div",
    { class: "tabs" },
    [
      ["open", "Ждут решения"],
      ["decided", "Решённые"],
    ].map(([k, text]) =>
      h(
        "button",
        {
          class: `tab ${tab === k ? "active" : ""}`,
          onClick: () => {
            tab = k;
            renderAppeals(root);
          },
        },
        text,
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
          h("thead", {}, h("tr", {}, ["Диалог", "Критерий", "Комментарий", "Итог", ""].map((t, i) => h("th", { class: i === 3 ? "num" : "" }, t)))),
          h(
            "tbody",
            {},
            list.map((a) =>
              h(
                "tr",
                {},
                h("td", { class: "cell-id" }, h("a", { href: `#/appeals/${a.id}`, class: "mono strong" }, a.conversation_id), h("div", { class: "muted small" }, dt(a.created_at))),
                h("td", {}, a.criterion_title),
                h("td", {}, h("div", { class: "preview" }, a.comment)),
                h("td", { class: "num" }, a.status === "open" ? scorePill(a.current_total, 100) : h("span", { class: "small" }, num(a.total_before, 2), " → ", h("b", {}, num(a.total_after, 2)))),
                h("td", { class: "cell-action" }, h("a", { class: `btn btn-sm ${a.status === "open" ? "btn-primary" : ""}`, href: `#/appeals/${a.id}` }, a.status === "open" ? "Рассмотреть" : "Открыть")),
              ),
            ),
          ),
        ),
      )
    : emptyState(tab === "open" ? "Нет апелляций, ждущих решения" : "Решённых апелляций пока нет", "Апелляцию подают в карточке диалога: раскройте критерий и нажмите «Оспорить».");

  setChildren(root, pageHeader({ title: "Апелляции" }), tabs, table);
}

export async function renderAppeal(root, _params, id) {
  const a = await api.appeal(Number(id));
  const c = a.criterion;
  const ev = a.evaluation;
  const original = a.original_item;
  const evidenceTurns = new Set((original.evidence || []).map((e) => e.turn));
  const range = original.checked_range;
  const decided = a.status !== "open";
  const cardHref = `#/c/${encodeURIComponent(a.conversation_id)}?eval=${ev.id}`;

  const fragment = h(
    "div",
    { class: "split-chat-list" },
    a.fragment.map((t) => chatMessage(t, { cls: evidenceTurns.has(t.idx) ? "hl-mid" : !evidenceTurns.size && range && t.idx >= range[0] && t.idx <= range[1] ? "hl-range" : "" })),
  );

  const dispute = h(
    "div",
    { class: "split-block" },
    h("div", { class: "block first" }, label("Оценка"), h("div", { class: "dispute-score" }, h("span", { class: "answer answer-na" }, answerLabel(c, original))), h("p", { class: "explanation" }, original.explanation)),
    h("div", { class: "block" }, label("Комментарий апелляции"), h("div", { class: "quote-box appeal-comment" }, a.comment)),
    h(
      "details",
      { class: "more" },
      h("summary", {}, "Описание критерия"),
      h("p", {}, c.description || "—"),
      h("p", { class: "small muted" }, `${c.type === "binary" ? "Да / Нет" : `Шкала ${c.scale.min}–${c.scale.max}`} · вес ${num(c.weight, 2)} · N/A ${c.na_allowed ? `допускается: ${c.na_rule || ""}` : "не допускается"}`),
    ),
  );

  const decision = h("div", { class: "split-block" });
  if (decided) {
    setChildren(
      decision,
      h("h2", {}, "Решение"),
      h(
        "dl",
        { class: "kv" },
        h("dt", {}, "Было"),
        h("dd", {}, answerLabel(c, original)),
        h("dt", {}, "Решение"),
        h("dd", {}, a.status === "changed" ? `изменено на «${answerLabel(c, { ...original, ...a.decided_item })}»` : "оценка подтверждена"),
        h("dt", {}, "Причина"),
        h("dd", {}, a.decision_reason),
        h("dt", {}, "Итог диалога"),
        h("dd", {}, num(a.total_before, 2), " → ", h("b", {}, num(a.total_after, 2))),
      ),
    );
  } else {
    let choice = "confirm";
    const values = [];
    for (let v = c.scale.min; v <= c.scale.max; v++) values.push([String(v), `${v}${c.scale.labels?.[String(v)] ? ` — ${c.scale.labels[String(v)]}` : ""}`]);
    if (c.na_allowed) values.push(["na", "N/A — неприменим"]);
    const valueSel = h("select", { class: "input" }, values.map(([v, l]) => h("option", { value: v, selected: v === (original.applicable ? String(original.value) : "na") }, l)));
    const valueField = field("Новая оценка", valueSel);
    valueField.hidden = true;
    const reason = h("textarea", { class: "input", rows: 3, placeholder: "Причина решения" });
    const radio = (val, text) =>
      h(
        "label",
        { class: "radio" },
        h("input", {
          type: "radio",
          name: "decision",
          value: val,
          checked: val === "confirm",
          onChange: () => {
            choice = val;
            valueField.hidden = val !== "change";
          },
        }),
        text,
      );
    setChildren(
      decision,
      h("h2", {}, "Решение"),
      h(
        "div",
        { class: "stack" },
        h("div", { class: "radio-group" }, radio("confirm", "Подтвердить"), radio("change", "Изменить")),
        valueField,
        field("Причина", reason),
        h(
          "button",
          {
            class: "btn-dark",
            onClick: async (e) => {
              if (reason.value.trim().length < 3) return toast("Укажите причину", "warn");
              e.currentTarget.disabled = true;
              try {
                const body = { decision: choice, reason: reason.value.trim() };
                if (choice === "change") {
                  body.applicable = valueSel.value !== "na";
                  body.value = body.applicable ? Number(valueSel.value) : null;
                }
                const r = await api.decide(a.id, body);
                toast(`Сохранено. Итог: ${num(r.total_before, 2)} → ${num(r.total_after, 2)}`, "ok");
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
      ),
    );
  }

  setChildren(
    root,
    h(
      "div",
      { class: "split" },
      h("section", { class: "split-left" }, h("a", { class: "split-back", href: "#/appeals" }, "← Апелляции"), fragment, h("a", { class: "split-back", href: cardHref }, "Открыть весь диалог →")),
      h(
        "section",
        { class: "split-right" },
        h("div", { class: "split-meta" }, `Апелляция #${a.id} · `, h("a", { href: cardHref }, a.conversation_id), ` · ${APPEAL_STATUS[a.status]}`),
        h("h1", { class: "split-title" }, c.title),
        dispute,
        decision,
      ),
    ),
  );
}
