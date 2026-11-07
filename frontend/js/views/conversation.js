// Dialogue card, layout "E · Сплит": chat on the left, score + criteria on the right.
// Each criterion is a button; selecting it highlights the graded lines in the chat and shows the reason.
import { api } from "../api.js";
import { refreshBadges, trackBatch } from "../app.js";
import { APPEAL_STATUS, CHANNEL, STATUS, answerLabel, chatMessage, dt, field, fmtOffset, h, modal, num, select, setChildren, spinner, toast } from "../ui.js";

export async function renderConversation(root, params, id) {
  const conv = await api.conversation(id);
  let evalId = Number(params.get("eval")) || conv.evaluations.find((e) => e.status === "done")?.id || conv.evaluations[0]?.id;
  let pollTimer = null;
  let selected = params.get("crit") || null;

  // ---------- chat (left) ----------
  const audio = conv.audio_url ? h("audio", { controls: true, preload: "none", src: api.audioUrl(conv.id), class: "audio" }) : null;
  const turnEls = new Map();
  const chat = h(
    "div",
    { class: "split-chat-list" },
    conv.transcript.map((t) => {
      const seekBtn = audio && t.offset_s !== null && t.speaker !== "system" ? h("button", { class: "seek-btn", title: "Прослушать", onClick: () => seek(t.offset_s, true) }, `▶ ${fmtOffset(t.offset_s)}`) : null;
      const el = chatMessage(t, { meta: [seekBtn] });
      turnEls.set(t.idx, el);
      return el;
    }),
  );

  function seek(offset, play) {
    if (!audio || offset === null || offset === undefined) return;
    const go = () => {
      audio.currentTime = offset;
      if (play) audio.play().catch(() => toast("Не удалось воспроизвести запись", "warn"));
    };
    if (audio.readyState >= 1) go();
    else {
      audio.preload = "auto";
      audio.addEventListener("loadedmetadata", go, { once: true });
      audio.load();
    }
  }

  /** Highlight graded lines. tone: "bad" | "good" | "mid" | "na" */
  function highlight(turns, range, tone, scroll = true) {
    turnEls.forEach((el) => el.classList.remove("hl-bad", "hl-good", "hl-mid", "hl-range", "flash", "dim"));
    const focus = new Set(turns);
    if (range) for (let i = range[0]; i <= range[1]; i++) turnEls.get(i)?.classList.add("hl-range");
    if (focus.size) turnEls.forEach((el, i) => !focus.has(i) && el.classList.add("dim"));
    focus.forEach((i) => turnEls.get(i)?.classList.add(`hl-${tone === "na" ? "mid" : tone}`));
    const first = turnEls.get(turns[0] ?? range?.[0]);
    if (first) {
      // Scroll only the chat panel (not the page) so the graded line sits in the middle.
      const top = first.offsetTop - chat.offsetTop - chat.clientHeight / 2 + first.offsetHeight / 2;
      chat.scrollTo({ top: Math.max(0, top), behavior: scroll ? "smooth" : "auto" });
      if (scroll) first.classList.add("flash");
    }
    const target = conv.transcript[turns[0] ?? range?.[0]];
    if (target && audio) seek(target.offset_s, false);
  }

  const left = h(
    "section",
    { class: "split-left" },
    h("a", { class: "split-back", href: "#/dialogs" }, "← Диалоги"),
    audio ? h("div", { class: "split-audio" }, audio) : null,
    chat,
  );

  // ---------- result (right) ----------
  const right = h("section", { class: "split-right" });

  async function loadEvaluation() {
    clearTimeout(pollTimer);
    if (!evalId) {
      setChildren(right, metaLine(), h("p", { class: "split-empty" }, "Диалог ещё не оценён."), actions(null));
      return;
    }
    const ev = await api.evaluation(evalId);
    if (ev.status === "pending" || ev.status === "running") {
      setChildren(right, metaLine(), spinner(`${STATUS[ev.status]}…`));
      pollTimer = setTimeout(loadEvaluation, 2000);
      return;
    }
    if (ev.status === "error") {
      setChildren(right, metaLine(), h("p", { class: "split-empty text-bad" }, `Ошибка оценки: ${ev.error}`), actions(null));
      return;
    }
    renderResult(ev);
    const turn = params.get("turn");
    if (turn !== null && turn !== "") highlight([Number(turn)], null, "mid");
  }

  function metaLine() {
    return h("div", { class: "split-meta" }, h("span", { class: "mono" }, conv.id), ` · ${CHANNEL[conv.channel] || conv.channel} · ${conv.language}`);
  }

  function actions(ev) {
    const history =
      conv.evaluations.length > 1
        ? select(
            conv.evaluations.map((e) => [e.id, `Оценка #${e.id} · ${e.status === "done" ? num(e.total, 2) : STATUS[e.status]} · ${dt(e.created_at)}`]),
            evalId,
            {
              class: "input split-history",
              title: "История оценок",
              onChange: (e) => {
                evalId = Number(e.target.value);
                selected = null;
                loadEvaluation();
              },
            },
          )
        : null;
    return h("div", { class: "split-actions" }, h("button", { class: "btn-flat", onClick: evaluateDialog }, ev ? "Оценить заново" : "Оценить"), history);
  }

  const MARK = { good: "✓", mid: "½", bad: "✕", na: "–" };

  /** Qwen v3: was the score decided by a code rule over the model's fields, or by the model itself? */
  function sourceLabel(item) {
    const agree = item.agreement !== undefined && item.agreement < 1 ? ` · согласие прогонов ${Math.round(item.agreement * 100)}%` : "";
    if (item.derived_by === "model") return `Решение: модель${agree}`;
    const ms = item.model_score === null || item.model_score === undefined ? "—" : item.model_score;
    return `Решение: правило «${item.derived_by.replace(/^rule:/, "")}» по полям модели (модель ответила «${ms}»)${agree}`;
  }

  function renderResult(ev) {
    const cfg = ev.checklist;
    const res = ev.final_result;
    const changed = ev.result.total !== res.total;

    const rows = cfg.criteria.map((c) => {
      const item = ev.final_items.find((it) => it.criterion_id === c.id);
      const row = res.criteria.find((r) => r.criterion_id === c.id) || {};
      const na = !item || !item.applicable;
      const tone = na ? "na" : row.ratio === 1 ? "good" : row.ratio === 0 ? "bad" : "mid";
      return { c, item, aiItem: ev.items.find((it) => it.criterion_id === c.id), row, tone, appeals: ev.appeals.filter((a) => a.criterion_id === c.id) };
    });
    // Nothing is selected until the user clicks a criterion (unless the URL asks for one).
    if (selected && !rows.some((r) => r.c.id === selected)) selected = null;

    /** Gross-error banner: what was found and where (not the criterion title, which may be phrased "no gross errors"). */
    function criticalBanner() {
      const found = rows.filter((r) => (res.critical_criteria || []).includes(r.c.id));
      return h(
        "div",
        { class: "split-critical" },
        ...found.map((r) => {
          const turns = (r.item?.evidence || []).map((e) => `#${e.turn}`);
          const why = r.item?.explanation || r.c.critical_rule || r.c.title;
          return h(
            "button",
            { class: "line-link", title: "Показать в диалоге", onClick: () => choose(r) },
            `Грубая ошибка${turns.length ? ` (реплика ${turns.join(", ")})` : ""}: ${why}`,
          );
        }),
      );
    }

    const detailBox = h("div", { class: "split-detail split-hint" }, "Нажмите на критерий — в чате подсветятся сообщения, по которым он оценён.");

    function choose(r, scroll = true) {
      selected = r.c.id;
      list.querySelectorAll(".crit-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === selected)));
      detailBox.classList.remove("split-hint");
      renderDetail(r);
      const turns = (r.item?.evidence || []).map((e) => e.turn);
      highlight(turns, turns.length ? null : r.item?.checked_range, r.tone, scroll);
    }

    function renderDetail(r) {
      const { c, item, aiItem, row, tone, appeals } = r;
      const openAppeal = appeals.find((a) => a.status === "open");
      const evidence = (item?.evidence || []).map((e) =>
        h("button", { class: "line-link", onClick: () => highlight([e.turn], null, tone) }, h("span", { class: "line-no" }, `#${e.turn}`), `«${e.quote}»`),
      );
      const suggestion = item?.suggested_reply || aiItem?.suggested_reply;
      const reason = item && !item.applicable && item.na_reason ? `Неприменимо: ${item.na_reason}` : (item?.overridden ? aiItem?.explanation : item?.explanation) || "—";
      setChildren(
        detailBox,
        h("div", { class: "detail-head" }, h("span", { class: `mark mark-${tone}` }, MARK[tone]), h("h3", {}, c.title), h("span", { class: "detail-answer" }, answerLabel(c, item))),
        item?.derived_by ? h("p", { class: "detail-source" }, sourceLabel(item)) : null,
        item?.overridden ? h("p", { class: "detail-note" }, `Изменено проверяющим (было «${answerLabel(c, aiItem)}»). ${item.reviewer_reason || ""}`) : null,
        h("p", { class: "detail-text" }, reason),
        evidence.length
          ? h("div", { class: "detail-lines" }, h("div", { class: "detail-label" }, tone === "bad" ? "Где ошибка" : "На чём основано"), evidence)
          : item?.checked_range
            ? h(
                "div",
                { class: "detail-lines" },
                h("div", { class: "detail-label" }, "Действие не найдено"),
                h("button", { class: "line-link", onClick: () => highlight([], item.checked_range, tone) }, `Проверены сообщения #${item.checked_range[0]}–#${item.checked_range[1]}`),
              )
            : null,
        tone === "bad" || tone === "mid"
          ? h(
              "div",
              { class: "detail-suggest" },
              h("div", { class: "detail-label" }, "Как следовало ответить"),
              suggestion ? h("p", {}, suggestion) : h("p", { class: "muted" }, "Пример правильного ответа появится здесь."),
            )
          : null,
        h(
          "div",
          { class: "detail-foot" },
          h("span", { class: "muted small" }, item?.applicable ? `${num(row.points, 1)} из ${num(row.max_points, 1)} баллов` : "вес перераспределён"),
          ...appeals.map((a) => h("a", { class: "small", href: `#/appeals/${a.id}` }, `Апелляция: ${APPEAL_STATUS[a.status].toLowerCase()}`)),
          h("button", { class: "btn-dark", disabled: !!openAppeal, onClick: () => appealDialog(c, item, ev) }, openAppeal ? "Апелляция подана" : "Оспорить"),
        ),
      );
    }

    const list = h(
      "ol",
      { class: "crit-buttons" },
      rows.map((r) =>
        h(
          "li",
          {},
          h(
            "button",
            { class: "crit-btn", dataset: { id: r.c.id }, "aria-pressed": String(r.c.id === selected), onClick: () => choose(r) },
            h("span", { class: `mark mark-${r.tone}` }, MARK[r.tone]),
            h("span", { class: "crit-btn-title" }, r.c.title),
            h("span", { class: "crit-btn-answer" }, answerLabel(r.c, r.item)),
          ),
        ),
      ),
    );

    setChildren(
      right,
      metaLine(),
      h(
        "div",
        { class: "split-score" },
        h("span", { class: "split-score-num" }, res.total === null ? "—" : num(res.total, 2)),
        h("span", { class: "split-score-max" }, `/ ${num(res.max_total, 0)}`),
        changed ? h("span", { class: "split-score-was" }, `было ${num(ev.result.total, 2)}`) : null,
      ),
      ev.summary ? h("p", { class: "split-summary" }, ev.summary) : null,
      res.critical_error ? criticalBanner() : null,
      res.sections?.length ? h("p", { class: "split-sections" }, res.sections.map((s) => `${s.title} ${s.na ? "—" : `${num(s.percent, 0)}%`}`).join("  ·  ")) : null,
      list,
      detailBox,
      actions(ev),
    );
    const current = rows.find((r) => r.c.id === selected);
    if (current) choose(current, false);
  }

  function appealDialog(c, item, ev) {
    const comment = h("textarea", { class: "input", rows: 4, placeholder: "Почему вы не согласны?" });
    modal({
      title: `Оспорить: ${c.title}`,
      body: h("div", { class: "stack" }, h("div", {}, "Сейчас: ", h("b", {}, answerLabel(c, item))), field("Комментарий", comment)),
      actions: [
        {
          label: "Отправить",
          kind: "primary",
          onClick: async () => {
            if (comment.value.trim().length < 3) throw new Error("Добавьте комментарий");
            const a = await api.createAppeal(ev.id, { criterion_id: c.id, comment: comment.value.trim() });
            toast(`Апелляция #${a.id} отправлена`, "ok");
            refreshBadges();
            loadEvaluation();
          },
        },
      ],
    });
  }

  async function evaluateDialog() {
    const lists = await api.checklists();
    const cl = select(
      lists.map((c) => [c.id, `${c.name} · v${c.version}${c.is_active ? " (активный)" : ""}`]),
      lists.find((c) => c.is_active)?.id,
    );
    modal({
      title: "Оценить диалог",
      body: field("Чек-лист", cl),
      actions: [
        {
          label: "Оценить",
          kind: "primary",
          onClick: async () => {
            const r = await api.evaluate({ conversation_ids: [conv.id], checklist_id: Number(cl.value) });
            trackBatch(r.batch_id);
            const fresh = await api.conversation(conv.id);
            conv.evaluations = fresh.evaluations;
            evalId = fresh.evaluations[0]?.id;
            selected = null;
            loadEvaluation();
          },
        },
      ],
    });
  }

  setChildren(root, h("div", { class: "split" }, left, right));
  setChildren(right, spinner());
  await loadEvaluation();
  return () => clearTimeout(pollTimer);
}
