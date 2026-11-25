/* The review dialog owns selection and edits. The bridge owns saved data. */
const reviewUI = (() => {
  const byId = id => document.getElementById(id);
  const dialog = byId("review-dialog");
  const feedback = byId("review-feedback");
  const status = byId("review-status");
  const saveSearch = byId("review-save-search");
  const exportButton = byId("review-export");
  const compareButton = byId("review-compare");
  const compareDialog = byId("review-compare-dialog");
  const accept = byId("review-accept-change");
  const choices = new Set();
  const answers = new WeakMap();
  const evidenceScopes = new Set(["structural_candidate", "literal_occurrence", "provider_reference"]);
  let candidates = [];
  let draft = null;
  let changedSearch = "";
  let busy = false;

  function sync() {
    saveSearch.disabled = !draft || searchBusy || busy;
    exportButton.disabled = !choices.size || busy || searchBusy || exportBusy;
    compareButton.disabled = choices.size !== 2 || busy || exportBusy;
  }

  function message(target, value, isError = false) {
    target.textContent = value;
    target.classList.toggle("is-error", isError);
  }

  async function run(action, target = status) {
    if (busy) return null;
    busy = true;
    sync();
    try {
      const answer = await action();
      if (answer?.error) message(target, errorText(answer.error, answer), true);
      return answer;
    } catch {
      message(target, errorText("review_storage"), true);
      return null;
    } finally {
      busy = false;
      sync();
    }
  }

  function element(tag, value = "") {
    const node = document.createElement(tag);
    node.textContent = value;
    return node;
  }

  function button(key, action) {
    const node = element("button", t(key));
    node.type = "button";
    node.className = "link-button";
    node.addEventListener("click", action);
    return node;
  }

  function capture(query, sources) {
    const builder = { steps: [], scope: "sentence", window: "" };
    if (query.mode === "pattern") {
      const exclByStepId = new Map();
      if (typeof document.querySelectorAll === "function") {
        document.querySelectorAll("#pattern-exclusions .pattern-exclusion-input").forEach(input => {
          if (input.dataset?.stepId) exclByStepId.set(input.dataset.stepId, input);
        });
      }
      builder.steps = [...document.querySelectorAll("#pattern-builder-steps .pattern-step")].map(
        (step, index) => {
          const stepId = step.dataset?.stepId || "";
          const exclEl = (stepId && exclByStepId.get(stepId)) || byId(`pattern-exclusion-${index}`);
          return {
            term: step.querySelector(".pattern-term-input").value,
            min: step.querySelector(".pattern-gap-min").value,
            max: step.querySelector(".pattern-gap-max").value,
            exclusion: exclEl?.value || "",
          };
        }
      );
      builder.scope = byId("pattern-scope").value;
      builder.window = byId("pattern-window").value;
    }
    draft = {
      query: { ...query }, sources: [...sources], builder,
      sort: byId("sort").value,
      options: {
        historydb_site: historydbSite, historydb_item: historydbItem,
        nahf_item: nahfItem, ships_database: shipsDatabase,
      },
    };
  }

  function card(node, row, section) {
    const answer = section ? answers.get(section.closest(".panel")) : null;
    if (!answer) return;
    const session = answer.session;
    const pointer = answer.snapshot_id
      ? { snapshot_id: answer.snapshot_id }
      : session && { session_id: session.id, revision: session.displayedRevision ?? session.revision };
    const id = session ? row.result_id : row.id;
    if (!pointer || !id) return;
    const save = button("review.save_candidate", async () => {
      save.disabled = true;
      const result = await run(() => api().save_review_candidate(pointer, id), feedback);
      if (result?.id) {
        save.textContent = t("review.saved");
        message(feedback, t("review.saved"));
      } else {
        save.disabled = false;
      }
    });
    save.classList.add("review-card-action");
    node.querySelector("header").append(save);
  }

  function queryLabel(query) {
    if (query.mode === "pair") return t("results.query_pair", query);
    return query.query || query.expression || query.pattern_file_name || query.pattern_file || "";
  }

  function setChangedSearch(id = "") {
    changedSearch = id;
    accept.classList.toggle("is-hidden", !id);
  }

  async function restore(id, confirmed = false) {
    if (searchBusy || isLocalBusy() || savingSources) {
      message(status, errorText("local_busy"), true);
      return;
    }
    setChangedSearch();
    message(status, t("sources.loading"));
    const result = await run(() => api().restore_review_search(id, confirmed));
    if (result?.error === "review_changed") {
      setChangedSearch(id);
      return;
    }
    if (!result || result.error) return;
    clearResults();
    clearPatternFileState();
    const query = result.query;
    form.querySelector(`input[name="mode"][value="${query.mode}"]`).checked = true;
    byId("first").value = query.first || "";
    byId("second").value = query.second || "";
    byId("query").value = query.query || "";
    if (query.mode === "pattern") {
      byId("pattern-builder-steps").replaceChildren();
      byId("pattern-exclusions").replaceChildren();
      for (const values of result.builder?.steps || []) {
        const step = addPatternStep();
        step.querySelector(".pattern-term-input").value = values.term || "";
        step.querySelector(".pattern-gap-min").value = values.min || "";
        step.querySelector(".pattern-gap-max").value = values.max || "";
        const index = byId("pattern-builder-steps").children.length - 1;
        const stepId = step.dataset?.stepId || "";
        const exclEl = (stepId && typeof document.querySelectorAll === "function"
          ? [...document.querySelectorAll("#pattern-exclusions .pattern-exclusion-input")]
            .find(input => input.dataset?.stepId === stepId)
          : null) || byId(`pattern-exclusion-${index}`);
        if (exclEl) exclEl.value = values.exclusion || "";
      }
      syncPatternExclusionCount();
      byId("pattern-scope").value = result.builder?.scope || "sentence";
      byId("pattern-window").value = result.builder?.window ?? "";
      syncPatternScopeWindow();
      patternFile = result.pattern_file;
      syncPatternFile();
    }
    library = result.library || library;
    localState = result.libraries;
    localState.selected = result.sources;
    applySourceOptions(result.options);
    if (result.sources.includes("docusky")) {
      docuskyBox.value = "docusky";
      docuskySelect.value = "docusky";
    }
    restoreSources();
    if (historydbCatalog.has(historydbSite)) fillHistorydbItems(historydbCatalog.get(historydbSite));
    if ([...nahfItemSelect.options].some(option => option.value === nahfItem)) {
      nahfItemSelect.value = nahfItem;
    }
    byId("sort").value = result.sort;
    showLibrary();
    updateHint();
    updateSource();
    dialog.close();
    showView("search");
    message(feedback, t("review.restored"));
  }

  function candidateItem(item) {
    const article = element("article");
    article.className = "review-item";
    const result = item.result;
    const label = element("label");
    const check = element("input");
    check.type = "checkbox";
    check.checked = choices.has(item.id);
    check.addEventListener("change", () => {
      if (check.checked) choices.add(item.id);
      else choices.delete(item.id);
      sync();
    });
    label.append(check, element("strong", result.title || result.text_id || item.source_id));
    article.append(label, element("p", `${sourceName(item.source_id)} · ${queryLabel(item.query)}`));
    if (/^https?:\/\//.test(result.link || "")) {
      article.append(button("review.source", () => openSourceLink(result.link)));
    }
    if (result.segment) {
      const excerpt = element("p");
      excerpt.lang = "lzh";
      excerpt.append(marked(result.segment));
      article.append(excerpt);
    }
    if (result.citation && typeof CITATION_FIELDS !== "undefined") {
      const parts = [];
      for (const [field, label] of CITATION_FIELDS) {
        if (result.citation[field]) parts.push(t(label, { value: result.citation[field] }));
      }
      if (parts.length) {
        const citation = element("p", parts.join(" · "));
        citation.className = "citation";
        article.append(citation);
      }
    }
    const field = element("label", t("review.note"));
    const note = element("textarea");
    note.value = item.note;
    note.maxLength = 10000;
    note.rows = 2;
    field.append(note);
    const state = element("select");
    state.setAttribute("aria-label", t("review.state"));
    for (const value of ["unreviewed", "keep", "exclude"]) state.add(new Option(t(`review.${value}`), value));
    state.value = item.status;
    article.append(field, state,
      button("review.save_note", async () => {
        const nextStatus = state.value;
        const nextNote = note.value;
        const result = await run(() => api().update_review_candidate(item.id, nextStatus, nextNote));
        if (result?.id) {
          item.status = nextStatus;
          item.note = nextNote;
          message(status, t("review.saved"));
        }
      }),
      button("review.remove", () => remove(item.id))
    );
    return article;
  }

  function comparisonCard(item) {
    const result = item.result;
    const input = item.provenance?.inputs?.find(entry => entry.text_id === result.text_id);
    const canonical = result.record?.schema === "cpe.result/1" || result.record?.schema === "cpe.occurrence/1";
    const record = !item.reference_only && canonical ? result.record : null;
    const span = record?.segment?.src;
    const position = Array.isArray(span) && span.length === 2 && span.every(Number.isInteger)
      ? `[${span[0]}, ${span[1]})` : "";
    const scope = evidenceScopes.has(result.evidence_scope) ? result.evidence_scope : "unknown";
    const missing = t("review.compare_not_recorded");
    const article = element("article");
    article.className = "review-item review-comparison-card";
    article.append(element("h3", result.title || result.text_id || item.source_id));

    if (!item.reference_only && result.segment) {
      const excerpt = element("p");
      excerpt.className = "text review-comparison-excerpt";
      excerpt.lang = "lzh";
      for (const part of ["before", "segment", "after"]) {
        if (!result[part]) continue;
        const text = element("span");
        text.className = part;
        text.append(marked(result[part]));
        excerpt.append(text);
      }
      article.append(excerpt);
    } else {
      article.append(element("p", item.reference_only ? t("review.compare_reference_only") : missing));
    }

    if (result.citation && typeof CITATION_FIELDS !== "undefined") {
      const parts = [];
      for (const [field, label] of CITATION_FIELDS) {
        if (result.citation[field]) parts.push(t(label, { value: result.citation[field] }));
      }
      if (parts.length) {
        const citation = element("p", parts.join(" · "));
        citation.className = "citation";
        article.append(citation);
      }
    }

    const details = element("dl");
    const source = item.reference_only ? sourceName(item.source_id) : input?.source || input?.path || item.source_id;
    const fields = [
      ["review.compare_source", source],
      ["review.compare_edition", input?.edition || missing],
      ["review.compare_position", position || missing],
      ["review.compare_evidence", t(`review.evidence.${scope}`)],
    ];
    if (record?.grade) fields.push(["review.compare_grade", record.grade]);
    for (const [key, value] of fields) {
      details.append(element("dt", t(key)), element("dd", value));
    }
    article.append(details);
    const link = result.link || input?.source;
    if (/^https?:\/\//.test(link || "")) {
      article.append(button("review.source", () => openSourceLink(link)));
    }
    return article;
  }

  function searchItem(item) {
    const article = element("article");
    article.className = "review-item";
    article.append(element("strong", queryLabel(item.query)));
    article.append(element("p", item.sources.map(sourceName).join(" · ")));
    article.append(button("review.restore", () => restore(item.id)), button("review.remove", () => remove(item.id)));
    return article;
  }

  async function render() {
    const answer = await run(() => api().review_items());
    if (!answer || answer.error || !dialog.open) return;
    candidates = answer.candidates;
    const ids = new Set(candidates.map(item => item.id));
    for (const id of choices) if (!ids.has(id)) choices.delete(id);
    byId("review-candidates").replaceChildren(
      ...(candidates.length ? candidates.map(candidateItem) : [element("p", t("review.empty_candidates"))])
    );
    byId("review-searches").replaceChildren(
      ...(answer.searches.length ? answer.searches.map(searchItem) : [element("p", t("review.empty_searches"))])
    );
    sync();
  }

  async function remove(id) {
    const answer = await run(() => api().remove_review_item(id));
    if (answer?.id) await render();
  }

  byId("review-open").addEventListener("click", async () => {
    dialog.showModal();
    message(status, t("sources.loading"));
    setChangedSearch();
    await render();
    if (status.textContent === t("sources.loading")) message(status, "");
  });
  dialog.addEventListener("cancel", event => { if (busy || exportBusy) event.preventDefault(); });
  dialog.querySelector(".dialog-close").addEventListener("click", event => {
    if (busy || exportBusy) event.preventDefault();
  });
  saveSearch.addEventListener("click", async () => {
    if (!draft || searchBusy) return;
    const answer = await run(() => api().save_review_search(draft), feedback);
    if (answer?.id) message(feedback, t("review.saved"));
  });
  accept.addEventListener("click", () => { if (changedSearch) void restore(changedSearch, true); });
  compareButton.addEventListener("click", () => {
    const selected = candidates.filter(item => choices.has(item.id));
    if (selected.length !== 2) return;
    byId("review-comparison").replaceChildren(...selected.map(comparisonCard));
    compareDialog.showModal();
  });
  exportButton.addEventListener("click", () => {
    if (busy || !choices.size) return;
    void exportSnapshots({ review_ids: [...choices], format: "jsonl", sort: "source" },
      (value, state) => message(status, value, state?.isError));
  });
  return {
    capture, card, sync,
    bind: (panel, answer) => answers.set(panel, answer),
    clear: () => { draft = null; sync(); },
  };
})();
