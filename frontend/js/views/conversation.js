import { api } from "../api.js";
import { refreshBadges, trackBatch } from "../app.js";
import {
  APPEAL_STATUS,
  CHANNEL,
  SPEAKER,
  STATUS,
  answerLabel,
  dt,
  emptyState,
  errorBox,
  field,
  fmtOffset,
  h,
  modal,
  money,
  num,
  scorePill,
  scoreTone,
  select,
  setChildren,
  spinner,
  toast,
  turnTime,
} from "../ui.js";

export async function renderConversation(root, params, id) {
  const conv = await api.conversation(id);
  const isCall = conv.channel === "voice call";
  let evalId = Number(params.get("eval")) || conv.evaluations.find((e) => e.status === "done")?.id || conv.evaluations[0]?.id;
  let pollTimer = null;

  // ---------- transcript ----------
  const audio = conv.audio_url ? h("audio", { controls: true, preload: "none", src: api.audioUrl(conv.id), class: "audio" }) : null;
  const turnEls = new Map();
  const transcriptList = h(
    "div",
    { class: "transcript", id: "transcript" },
    conv.transcript.map((t) => {
      const el = h(
        "div",
        { class: `turn turn-${t.speaker}`, id: `turn-${t.idx}`, dataset: { idx: t.idx } },
        h(
          "div",
          { class: "turn-meta" },
          h("a", { class: "turn-idx", href: `#/c/${encodeURIComponent(conv.id)}?turn=${t.idx}${evalId ? `&eval=${evalId}` : ""}`, title: "Ссылка на реплику", onClick: (e) => { e.preventDefault(); focusTurns([t.idx]); } }, `#${t.idx}`),
          h("span", { class: "turn-speaker" }, SPEAKER[t.speaker] || t.speaker),
          h("span", { class: "turn-time" }, turnTime(t)),
          audio && t.offset_s !== null ? h("button", { class: "seek-btn", title: "Прослушать с этого места", onClick: () => seek(t.offset_s, true) }, `▶ ${fmtOffset(t.offset_s)}`) : null,
          t.speaker === "bot" || t.speaker === "system" ? h("span", { class: "turn-context" }, "контекст") : null,
        ),
        h("div", { class: "turn-text" }, t.text),
      );
      turnEls.set(t.idx, el);
      return el;
    }),
  );

  function seek(offset, play) {
    if (!audio || offset === null || offset === undefined) return;
    const go = () => {
      audio.currentTime = offset;
      if (play) audio.play().catch(() => toast("Не удалось воспроизвести запись — откройте плеер организаторов", "warn"));
    };
    if (audio.readyState >= 1) go();
    else {
      audio.preload = "auto";
      audio.addEventListener("loadedmetadata", go, { once: true });
      audio.load();
    }
  }

  function focusTurns(turns, range, { scroll = true, playAudio = false } = {}) {
    turnEls.forEach((el) => el.classList.remove("hl-evidence", "hl-range", "flash"));
    if (range) for (let i = range[0]; i <= range[1]; i++) turnEls.get(i)?.classList.add("hl-range");
    turns.forEach((i) => turnEls.get(i)?.classList.add("hl-evidence"));
    const first = turnEls.get(turns[0] ?? range?.[0]);
    if (first && scroll) {
      first.scrollIntoView({ behavior: "smooth", block: "center" });
      first.classList.add("flash");
    }
    const target = conv.transcript[turns[0] ?? range?.[0]];
    if (target && audio) seek(target.offset_s, playAudio);
    const p = new URLSearchParams();
    if (evalId) p.set("eval", evalId);
    if (turns[0] !== undefined) p.set("turn", turns[0]);
    history.replaceState(null, "", `#/c/${encodeURIComponent(conv.id)}?${p}`);
  }

  const transcriptPanel = h(
    "section",
    { class: "panel transcript-panel" },
    h("div", { class: "panel-head" }, h("h2", {}, "Транскрипт"), h("span", { class: "muted small" }, `${conv.transcript.length} реплик`)),
    audio
      ? h(
          "div",
          { class: "audio-box" },
          audio,
          h(
            "div",
            { class: "small muted" },
            "Нажмите ▶ у реплики или ссылку-доказательство, чтобы перейти к фрагменту записи. ",
            h("a", { href: conv.audio_url, target: "_blank", rel: "noreferrer" }, "Открыть плеер организаторов ↗"),
          ),
        )
      : null,
    h("div", { class: "legend small" }, h("span", { class: "lg lg-evidence" }, "доказательство"), h("span", { class: "lg lg-range" }, "проверенный участок"), h("span", { class: "muted" }, "Бот и система — контекст, не оцениваются")),
    transcriptList,
  );

  // ---------- result ----------
  const resultPanel = h("section", { class: "panel result-panel" });
  const historySel = h("div", { class: "eval-history" });

  async function loadEvaluation() {
    clearTimeout(pollTimer);
    if (!evalId) {
      setChildren(resultPanel, emptyState("Диалог ещё не оценён", "Выберите чек-лист и запустите оценку.", h("button", { class: "btn btn-primary", onClick: evaluateDialog }, "Оценить")));
      return;
    }
    const ev = await api.evaluation(evalId);
    renderHistory();
    if (ev.status === "pending" || ev.status === "running") {
      setChildren(resultPanel, spinner(`${STATUS[ev.status]}… (${ev.checklist_name})`));
      pollTimer = setTimeout(loadEvaluation, 2000);
      return;
    }
    if (ev.status === "error") {
      setChildren(resultPanel, errorBox(`Ошибка оценки: ${ev.error}`), h("button", { class: "btn", onClick: evaluateDialog }, "Повторить оценку"));
      return;
    }
    renderResult(ev);
    const turn = params.get("turn");
    if (turn !== null && turn !== "") focusTurns([Number(turn)]);
  }

  function renderHistory() {
    if (conv.evaluations.length < 1) return setChildren(historySel);
    setChildren(historySel, 
      select(
        conv.evaluations.map((e) => [e.id, `#${e.id} · ${e.checklist_name || "чек-лист"} · ${e.status === "done" ? num(e.total, 2) : STATUS[e.status]} · ${dt(e.created_at)}`]),
        evalId,
        {
          title: "История оценок",
          onChange: (e) => {
            evalId = Number(e.target.value);
            history.replaceState(null, "", `#/c/${encodeURIComponent(conv.id)}?eval=${evalId}`);
            loadEvaluation();
          },
        },
      ),
    );
  }

  function renderResult(ev) {
    const cfg = ev.checklist;
    const res = ev.final_result;
    const orig = ev.result;
    const critTitles = (res.critical_criteria || []).map((cid) => cfg.criteria.find((c) => c.id === cid)?.title || cid);
    const totalWeight = cfg.criteria.reduce((s, c) => s + c.weight, 0);
    const changed = orig.total !== res.total;

    const head = h(
      "div",
      { class: "score-card" },
      h(
        "div",
        { class: "score-main" },
        h("div", { class: `score-big tone-${scoreTone(res.total, res.max_total)}` }, res.total === null ? "—" : num(res.total, 2), h("small", {}, ` / ${num(res.max_total, 0)}`)),
        h(
          "div",
          {},
          h("div", { class: "strong" }, changed ? "Итоговая оценка (после апелляций)" : "Итоговая оценка"),
          changed ? h("div", { class: "small" }, "Исходная оценка ИИ: ", h("b", {}, num(orig.total, 2)), " → итог: ", h("b", {}, num(res.total, 2))) : null,
          h("div", { class: "small muted" }, `${cfg.name} · ${ev.evaluator === "mock" ? "MOCK-оценщик" : ev.model}`),
        ),
      ),
      res.critical_error
        ? h("div", { class: "critical-banner" }, h("b", {}, "⚠ Грубая ошибка: "), critTitles.join(", "), cfg.scoring?.critical_policy === "zero_total" ? " — итог обнулён по правилам чек-листа" : " — отмечено отдельно, итог не обнуляется")
        : null,
      ev.summary ? h("p", { class: "summary" }, ev.summary) : null,
      res.sections?.length
        ? h(
            "div",
            { class: "sections" },
            res.sections.map((s) =>
              h(
                "div",
                { class: "section-row" },
                h("span", { class: "section-title" }, s.title, s.critical ? h("span", { class: "badge badge-bad" }, "грубая ошибка") : null),
                s.na ? h("span", { class: "muted small" }, "N/A") : h("span", { class: "bar" }, h("span", { class: `tone-bg-${scoreTone(s.percent, 100)}`, style: { width: `${s.percent || 0}%` } })),
                h("span", { class: "num small" }, s.na ? "" : `${num(s.percent, 0)}% · ${num(s.points, 2)}/${num(s.max_points, 2)}`),
              ),
            ),
          )
        : null,
      h(
        "div",
        { class: "meta-row small muted" },
        h("span", {}, `Стоимость: ${money(ev.usage?.cost_usd)}`),
        ev.usage?.input_tokens ? h("span", {}, `токены: ${num(ev.usage.input_tokens + (ev.usage.cache_read_input_tokens || 0) + (ev.usage.cache_creation_input_tokens || 0), 0)} вх / ${num(ev.usage.output_tokens, 0)} вых`) : null,
        ev.duration_s ? h("span", {}, `${num(ev.duration_s, 1)} с`) : null,
        h("span", {}, dt(ev.finished_at)),
      ),
    );

    const items = cfg.criteria.map((c) => {
      const item = ev.final_items.find((i) => i.criterion_id === c.id);
      const aiItem = ev.items.find((i) => i.criterion_id === c.id);
      const row = res.criteria.find((r) => r.criterion_id === c.id) || {};
      const appeals = ev.appeals.filter((a) => a.criterion_id === c.id);
      return criterionCard(c, item, aiItem, row, appeals, ev, totalWeight, cfg);
    });

    setChildren(resultPanel, 
      h("div", { class: "panel-head" }, h("h2", {}, "Результат оценки"), h("span", { class: "muted small" }, `оценка #${ev.id}`)),
      head,
      h("div", { class: "criteria" }, items),
    );
  }

  function criterionCard(c, item, aiItem, row, appeals, ev, totalWeight, cfg) {
    const section = cfg.sections.find((s) => s.id === c.section_id);
    const nominal = (c.weight / totalWeight) * cfg.scoring.max_total;
    const na = !item || !item.applicable;
    const ratio = row.ratio;
    const tone = na ? "na" : ratio === 1 ? "good" : ratio === 0 ? "bad" : "mid";
    const openAppeal = appeals.find((a) => a.status === "open");
    const evidenceTurns = (item?.evidence || []).map((e) => e.turn);

    const evidence = (item?.evidence || []).map((e) =>
      h(
        "a",
        {
          class: `evidence ${e.verified ? "" : "evidence-unverified"}`,
          href: `#/c/${encodeURIComponent(conv.id)}?eval=${ev.id}&turn=${e.turn}`,
          title: e.verified ? "Перейти к реплике" : "Цитата не найдена дословно — проверьте реплику",
          onClick: (evt) => {
            evt.preventDefault();
            focusTurns([e.turn], item.checked_range);
          },
        },
        h("span", { class: "evidence-idx" }, `#${e.turn}`),
        h("span", { class: "evidence-speaker" }, SPEAKER[e.speaker] || ""),
        `«${e.quote}»`,
        isCall && conv.transcript[e.turn]?.offset_s !== null ? h("span", { class: "evidence-time" }, `▶ ${fmtOffset(conv.transcript[e.turn].offset_s)}`) : null,
      ),
    );

    return h(
      "article",
      { class: `criterion tone-border-${tone}`, id: `crit-${c.id}` },
      h(
        "div",
        { class: "criterion-head" },
        h(
          "div",
          {},
          h("div", { class: "criterion-title" }, c.title, c.critical ? h("span", { class: "badge badge-bad-outline", title: c.critical_rule || "Нарушение — грубая ошибка" }, "критичный") : null),
          h("div", { class: "small muted" }, [section?.title, c.type === "binary" ? "Да/Нет" : `шкала ${c.scale.min}–${c.scale.max}`].filter(Boolean).join(" · ")),
        ),
        h("div", { class: "criterion-score" }, h("span", { class: `answer answer-${tone}` }, answerLabel(c, item)), h("div", { class: "small muted" }, na ? "вес перераспределён" : `${num(row.points, 2)} из ${num(row.max_points, 2)} б.`)),
      ),
      h(
        "div",
        { class: "weights small muted" },
        `Вес: ${num(c.weight, 2)} (${num(nominal, 1)} б.)`,
        !na && Math.abs((row.effective_weight || 0) - nominal) > 0.01 ? ` → ${num(row.effective_weight, 2)} б. с учётом N/A` : "",
      ),
      item?.overridden
        ? h(
            "div",
            { class: "override" },
            h("b", {}, "Изменено проверяющим. "),
            `Исходно (ИИ): ${answerLabel(c, aiItem)} → решение: ${answerLabel(c, item)}. `,
            item.reviewer_reason ? h("span", {}, `Причина: ${item.reviewer_reason}`) : null,
          )
        : null,
      na && item?.na_reason ? h("p", { class: "na-reason" }, h("b", {}, "N/A: "), item.na_reason) : null,
      h("p", { class: "explanation" }, (item?.overridden ? aiItem?.explanation : item?.explanation) || "—"),
      evidence.length ? h("div", { class: "evidence-list" }, evidence) : null,
      item?.checked_range && (!evidence.length || !na)
        ? h(
            "a",
            {
              class: "checked-range",
              href: `#/c/${encodeURIComponent(conv.id)}?eval=${ev.id}&turn=${item.checked_range[0]}`,
              onClick: (evt) => {
                evt.preventDefault();
                focusTurns(evidenceTurns, item.checked_range);
              },
            },
            evidence.length ? `Проверенный участок: #${item.checked_range[0]}–#${item.checked_range[1]}` : `Действие не найдено. Проверенный участок: #${item.checked_range[0]}–#${item.checked_range[1]}`,
          )
        : null,
      h(
        "div",
        { class: "criterion-foot" },
        item?.confidence === "low" ? h("span", { class: "badge badge-warn", title: "Модель не уверена — рекомендуется ручная проверка" }, "Нужна проверка") : null,
        ...(item?.warnings || []).map((w) => h("span", { class: "badge", title: w }, "⚠ ", w)),
        ...appeals.map((a) => h("a", { class: `badge appeal-badge appeal-${a.status}`, href: `#/appeals/${a.id}` }, `Апелляция #${a.id}: ${APPEAL_STATUS[a.status]}`)),
        h("span", { class: "spacer" }),
        h("button", { class: "btn btn-sm", disabled: !!openAppeal, title: openAppeal ? "Апелляция уже на рассмотрении" : "Оспорить оценку критерия", onClick: () => appealDialog(c, item, ev) }, "Оспорить"),
      ),
    );
  }

  function appealDialog(c, item, ev) {
    const comment = h("textarea", { class: "input", rows: 4, placeholder: "Почему вы не согласны с оценкой? Укажите реплики, если нужно (например, #12)." });
    const author = h("input", { class: "input", placeholder: "Имя (необязательно)" });
    modal({
      title: `Апелляция: ${c.title}`,
      body: h(
        "div",
        { class: "stack" },
        h("div", { class: "quote-box" }, h("div", {}, "Текущая оценка: ", h("b", {}, answerLabel(c, item))), h("div", { class: "small muted" }, item?.explanation || "")),
        field("Комментарий", comment),
        field("Автор", author),
      ),
      actions: [
        {
          label: "Подать апелляцию",
          kind: "primary",
          onClick: async () => {
            if (comment.value.trim().length < 3) throw new Error("Добавьте комментарий");
            const a = await api.createAppeal(ev.id, { criterion_id: c.id, comment: comment.value.trim(), author: author.value.trim() || null });
            toast(`Апелляция #${a.id} отправлена проверяющему`, "ok");
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
      body: h("div", { class: "stack" }, field("Чек-лист", cl), h("p", { class: "muted small" }, "Новая оценка добавится в историю, предыдущие сохранятся.")),
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
            loadEvaluation();
          },
        },
      ],
    });
  }

  setChildren(root, 
    h(
      "div",
      { class: "page-head" },
      h(
        "div",
        {},
        h("a", { href: "#/", class: "back small" }, "← Диалоги"),
        h("h1", { class: "mono" }, conv.id),
        h("div", { class: "chips" }, h("span", { class: `chip chip-${isCall ? "call" : "chat"}` }, CHANNEL[conv.channel] || conv.channel), h("span", { class: "chip" }, conv.language), h("span", { class: "chip" }, conv.group)),
      ),
      h("div", { class: "actions" }, historySel, h("button", { class: "btn btn-primary", onClick: evaluateDialog }, "Оценить")),
    ),
    h("div", { class: "card-grid" }, transcriptPanel, resultPanel),
  );
  setChildren(resultPanel, spinner());
  await loadEvaluation();
  if (!evalId && params.get("turn")) focusTurns([Number(params.get("turn"))]);
  return () => clearTimeout(pollTimer);
}
