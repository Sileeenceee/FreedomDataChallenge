// Small DOM toolkit: no framework, everything is created with textContent (transcripts are untrusted).

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "hidden" || k === "open") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

/** Like el.replaceChildren but flattens arrays and skips null/false (replaceChildren would stringify them). */
export function setChildren(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

/** Dark-green page band (same layout as the task guide): eyebrow, title, subtitle, actions. */
export function hero({ eyebrow, title, subtitle, back, actions, chips, titleClass }) {
  return h(
    "section",
    { class: "hero" },
    h(
      "div",
      { class: "hero-inner" },
      h(
        "div",
        { class: "hero-text" },
        back ? h("a", { class: "hero-back", href: back.href }, "← ", back.label) : null,
        eyebrow ? h("div", { class: "eyebrow" }, eyebrow) : null,
        h("h1", { class: titleClass }, title),
        subtitle ? h("p", { class: "hero-sub" }, subtitle) : null,
        chips ? h("div", { class: "hero-chips" }, chips) : null,
      ),
      actions ? h("div", { class: "hero-actions" }, actions) : null,
    ),
  );
}

/** One chat message, Telegram-style: client on the left, operator/bot on the right, system centred. */
export function chatMessage(t, { meta = [], cls = "" } = {}) {
  if (t.speaker === "system") {
    return h("div", { class: `msg msg-system ${cls}`, id: `turn-${t.idx}` }, h("span", { class: "service" }, t.text, h("span", { class: "service-time" }, ` · ${turnTime(t)}`)));
  }
  const side = t.speaker === "client" ? "left" : "right";
  return h(
    "div",
    { class: `msg msg-${side} msg-${t.speaker} ${cls}`, id: `turn-${t.idx}` },
    h(
      "div",
      { class: "bubble" },
      t.speaker === "bot" ? h("div", { class: "msg-author" }, "Бот") : null,
      h("div", { class: "msg-text" }, t.text),
      h("div", { class: "msg-meta" }, meta, h("span", {}, `#${t.idx}`), h("span", {}, turnTime(t))),
    ),
  );
}

/** Slim page title row (used on every page except the start page). */
export function pageHeader({ back, title, titleClass, subtitle, chips, actions }) {
  return h(
    "div",
    { class: "page-header" },
    h(
      "div",
      { class: "page-header-text" },
      back ? h("a", { class: "page-back", href: back.href }, "← ", back.label) : null,
      h("div", { class: "page-title-row" }, h("h1", { class: titleClass }, title), chips ? h("div", { class: "chips" }, chips) : null),
      subtitle ? h("p", { class: "page-sub" }, subtitle) : null,
    ),
    actions ? h("div", { class: "actions" }, actions) : null,
  );
}

export const stepNum = (n, done) => h("span", { class: `step-num ${done ? "step-done" : ""}` }, done ? "✓" : String(n).padStart(2, "0"));

/** Numbered workflow steps: [{ title, text, done, actions }] */
export function steps(items) {
  return h(
    "ol",
    { class: "steps" },
    items.map((s, i) =>
      h(
        "li",
        { class: `step ${s.done ? "is-done" : ""} ${s.current ? "is-current" : ""}` },
        stepNum(i + 1, s.done),
        h("div", { class: "step-body" }, h("div", { class: "step-title" }, s.title), s.text ? h("div", { class: "step-text" }, s.text) : null, s.actions ? h("div", { class: "step-actions" }, s.actions) : null),
      ),
    ),
  );
}

/** Soft-green note box, like "Вес и итог" in the guide. kind: info | warn */
export function callout(title, body, kind = "info") {
  return h("div", { class: `callout callout-${kind}` }, title ? h("div", { class: "callout-title" }, title) : null, h("div", { class: "callout-body" }, body));
}

/** Small uppercase label above a block (Ответ, Баллы, Обоснование…). */
export const label = (text) => h("div", { class: "label" }, text);

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function toast(message, kind = "info", ms = 3800) {
  const el = h("div", { class: `toast toast-${kind}` }, message);
  $("#toasts").append(el);
  setTimeout(() => el.classList.add("toast-hide"), ms);
  setTimeout(() => el.remove(), ms + 400);
}

export function modal({ title, body, actions = [], wide = false }) {
  const close = () => backdrop.remove();
  const footer = h(
    "div",
    { class: "modal-footer" },
    h("button", { class: "btn", onClick: close }, "Отмена"),
    actions.map((a) =>
      h(
        "button",
        {
          class: `btn ${a.kind === "primary" ? "btn-primary" : a.kind === "danger" ? "btn-danger" : ""}`,
          onClick: async (e) => {
            const btn = e.currentTarget;
            btn.disabled = true;
            try {
              const keepOpen = await a.onClick(close);
              if (keepOpen !== true) close();
            } catch (err) {
              toast(err.message, "error");
            } finally {
              btn.disabled = false;
            }
          },
        },
        a.label,
      ),
    ),
  );
  const box = h(
    "div",
    { class: `modal ${wide ? "modal-wide" : ""}`, role: "dialog", "aria-modal": "true" },
    h("div", { class: "modal-header" }, h("h3", {}, title), h("button", { class: "icon-btn", onClick: close, "aria-label": "Закрыть" }, "×")),
    h("div", { class: "modal-body" }, body),
    footer,
  );
  const backdrop = h("div", { class: "modal-backdrop", onMousedown: (e) => e.target === backdrop && close() }, box);
  document.body.append(backdrop);
  const first = box.querySelector("textarea, input, select");
  if (first) setTimeout(() => first.focus(), 30);
  return close;
}

export const spinner = (label = "Загрузка…") => h("div", { class: "spinner-row" }, h("span", { class: "spinner" }), label);

export function errorBox(message) {
  return h("div", { class: "error-box" }, message);
}

export function num(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

export function money(v) {
  if (v === null || v === undefined) return "—";
  return `$${Number(v).toFixed(v < 0.1 ? 4 : 3)}`;
}

export function dt(v) {
  if (!v) return "—";
  const d = new Date(v);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function scoreTone(total, max) {
  if (total === null || total === undefined) return "none";
  const p = (total / (max || 100)) * 100;
  return p >= 85 ? "good" : p >= 65 ? "mid" : "bad";
}

export function scorePill(total, max, extra) {
  return h("span", { class: `score-pill tone-${scoreTone(total, max)}` }, total === null || total === undefined ? "—" : num(total, 2), extra);
}

export const SPEAKER = { operator: "Оператор", client: "Клиент", bot: "Бот", system: "Система" };
export const CHANNEL = { "agent chat": "Чат", "voice call": "Звонок" };
export const STATUS = { pending: "В очереди", running: "Оценивается", done: "Готово", error: "Ошибка" };
export const APPEAL_STATUS = { open: "На рассмотрении", confirmed: "Оценка подтверждена", changed: "Оценка изменена" };

/** Human label for a criterion answer: "Да", "2 из 3 · В основном", "N/A". */
export function answerLabel(crit, item) {
  if (!item) return "—";
  if (!item.applicable || item.value === null || item.value === undefined) return "N/A";
  const label = crit && crit.scale && crit.scale.labels ? crit.scale.labels[String(item.value)] : null;
  if (crit && crit.type === "binary") return label || (item.value ? "Да" : "Нет");
  const max = crit && crit.scale ? crit.scale.max : "?";
  return `${item.value} из ${max}${label ? ` · ${label}` : ""}`;
}

export function fmtOffset(sec) {
  if (sec === null || sec === undefined) return "";
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Short display time for a turn: chats keep MM:SS, calls show HH:MM:SS from the ISO timestamp. */
export function turnTime(t) {
  const ts = t.timestamp || "";
  const m = ts.match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : ts;
}

export function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", { class: "field-label" }, label), control, hint ? h("span", { class: "field-hint" }, hint) : null);
}

export function select(options, value, props = {}) {
  return h(
    "select",
    { class: "input", ...props },
    options.map((o) => {
      const [v, label] = Array.isArray(o) ? o : [o, o];
      return h("option", { value: v, selected: String(v) === String(value) }, label);
    }),
  );
}

export function emptyState(title, text, action) {
  return h("div", { class: "empty" }, h("div", { class: "empty-title" }, title), text ? h("p", {}, text) : null, action || null);
}
