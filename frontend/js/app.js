import { api } from "./api.js";
import { $, $$, callout, errorBox, h, spinner, toast, setChildren } from "./ui.js";
import { renderAppeal, renderAppeals } from "./views/appeals.js";
import { renderChecklistEditor, renderChecklists } from "./views/checklists.js";
import { renderConversation } from "./views/conversation.js";
import { renderConversations } from "./views/conversations.js";
import { renderHome } from "./views/home.js";

const EMBED = new URLSearchParams(location.search).has("embed");
if (EMBED) document.body.classList.add("embed");

const routes = [
  [/^\/$/, renderHome],
  [/^\/dialogs$/, renderConversations],
  [/^\/c\/([^/]+)$/, renderConversation],
  [/^\/appeals$/, renderAppeals],
  [/^\/appeals\/(\d+)$/, renderAppeal],
  [/^\/checklists$/, renderChecklists],
  [/^\/checklists\/(new|\d+)$/, renderChecklistEditor],
];

export const state = { health: null, batches: new Set(), cleanup: null };

export function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query] = raw.split("?");
  return { path: path || "/", params: new URLSearchParams(query || "") };
}

export function go(hash) {
  if (location.hash === hash) router();
  else location.hash = hash;
}

async function router() {
  const { path, params } = parseHash();
  if (state.cleanup) {
    state.cleanup();
    state.cleanup = null;
  }
  $$("#nav a").forEach((a) => {
    const r = a.dataset.route;
    a.classList.toggle("active", r === "/" ? path === "/" : r === "/dialogs" ? path === "/dialogs" || path.startsWith("/c/") : path.startsWith(r));
  });
  const root = $("#app");
  for (const [re, view] of routes) {
    const m = path.match(re);
    if (!m) continue;
    setChildren(root, spinner());
    try {
      const cleanup = await view(root, params, ...m.slice(1).map(decodeURIComponent));
      if (typeof cleanup === "function") state.cleanup = cleanup;
    } catch (e) {
      console.error(e);
      setChildren(root, errorBox(`Ошибка: ${e.message}`));
    }
    return;
  }
  setChildren(root, errorBox("Страница не найдена"), h("p", {}, h("a", { href: "#/" }, "К списку диалогов")));
}

// --- batch progress (evaluations run in the background on the server) ------------------------------
let batchTimer = null;
export function trackBatch(batchId) {
  if (!batchId) return;
  state.batches.add(batchId);
  pollBatches();
}

async function pollBatches() {
  clearTimeout(batchTimer);
  const box = $("#batch-progress");
  if (!state.batches.size) {
    box.hidden = true;
    return;
  }
  let total = 0;
  let finished = 0;
  let errors = 0;
  let cost = 0;
  for (const id of [...state.batches]) {
    try {
      const b = await api.batch(id);
      total += b.total;
      finished += b.finished;
      errors += b.counts.error || 0;
      cost += b.cost_usd || 0;
      if (b.finished === b.total) {
        state.batches.delete(id);
        toast(`Оценка завершена: ${b.total} диалог(ов)${b.counts.error ? `, ошибок: ${b.counts.error}` : ""}`, b.counts.error ? "warn" : "ok");
        window.dispatchEvent(new CustomEvent("cqa:batch-finished", { detail: b }));
      }
    } catch {
      state.batches.delete(id);
    }
  }
  if (total) {
    box.hidden = false;
    const pct = Math.round((finished / total) * 100);
    setChildren(box, 
      h("span", {}, `Оценка ${finished}/${total}`),
      h("span", { class: "bar" }, h("span", { style: { width: `${pct}%` } })),
      errors ? h("span", { class: "text-bad" }, `ошибок: ${errors}`) : null,
      cost ? h("span", { class: "muted" }, `$${cost.toFixed(3)}`) : null,
    );
    window.dispatchEvent(new CustomEvent("cqa:batch-progress"));
  }
  if (state.batches.size) batchTimer = setTimeout(pollBatches, 1500);
  else box.hidden = true;
  refreshBadges();
}

export const isMock = () => !state.health || state.health.evaluator === "mock";

/** Shown wherever scores appear while no real evaluator is connected. */
export function mockNotice() {
  if (!isMock()) return null;
  return callout(
    "Демо-режим: оценки условные",
    "Сейчас подключён MOCK-оценщик — баллы, объяснения и доказательства сгенерированы заглушкой, а не моделью, и не отражают качество диалога. Подключите модель в backend/app/services/evaluator.py.",
    "warn",
  );
}

export async function refreshBadges() {
  try {
    const s = await api.stats();
    const badge = $("#appeals-badge");
    badge.hidden = !s.appeals_open;
    badge.textContent = s.appeals_open || "";
  } catch {
    /* ignore */
  }
}

async function init() {
  try {
    state.health = await api.health();
    const chip = $("#engine");
    if (!isMock()) {
      chip.textContent = `Модель: ${state.health.model}`;
      chip.classList.add("engine-ai");
      chip.title = "Оценка выполняется моделью";
    } else {
      chip.textContent = "Оценщик: демо (MOCK)";
      chip.classList.add("engine-mock");
      chip.title = "Модель не подключена — баллы условные. См. backend/app/services/evaluator.py";
    }
  } catch (e) {
    $("#engine").textContent = "API недоступен";
    $("#engine").classList.add("engine-mock");
  }
  refreshBadges();
  window.addEventListener("hashchange", router);
  router();
}

init();
