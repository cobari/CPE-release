// Search window behaviour. Every visible word comes from the catalog of the chosen
// language (AGENTS.md "Language"); source text is shown exactly as the server sends it,
// whatever the language, so choosing zh-Hans never converts characters.
"use strict";

const PAGE = 100; // bounded cards per result page
let catalog = {};

// Base styles are always readable. The viewport observer also clips against each
// result list, so both page scrolling and scrolling inside a panel trigger entry.
const resultMotion = (() => {
  const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
  let seen = new WeakSet();
  const pending = new Map();
  const running = new Map();
  const observer = typeof IntersectionObserver === "function"
    ? new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const item = pending.get(entry.target);
      pending.delete(entry.target);
      observer.unobserve(entry.target);
      if (!item || seen.has(item.identity)) return;
      seen.add(item.identity);
      if (preference.matches || !entry.target.isConnected || !entry.target.animate) return;
      const animation = entry.target.animate(
        [{ transform: "translateY(8px)" }, { transform: "translateY(0)" }],
        { duration: 240, delay: item.delay, easing: "cubic-bezier(.2,.7,.2,1)" },
      );
      running.set(entry.target, animation);
      const finished = () => { if (running.get(entry.target) === animation) running.delete(entry.target); };
      animation.finished.then(finished, finished);
    }), { threshold: 0 })
    : null;
  const stop = () => {
    observer?.disconnect();
    pending.clear();
    running.forEach(animation => animation.cancel());
    running.clear();
  };
  preference.addEventListener("change", () => { if (preference.matches) stop(); });
  return {
    reset() { stop(); seen = new WeakSet(); },
    forget(container) {
      pending.forEach((_, node) => {
        if (container.contains(node)) { observer?.unobserve(node); pending.delete(node); }
      });
      running.forEach((animation, node) => {
        if (container.contains(node)) { animation.cancel(); running.delete(node); }
      });
    },
    observe(node, identity = node) {
      // Results appear in place; the screen switch is the only transition.
      seen.add(identity);
    },
  };
})();

// Translated source links and import status can change the fixed dock's height.
// Reserve its actual size so the last card remains reachable at every width.
const dock = document.querySelector(".dock");
let dockFrame = null;
// Writing padding during resize delivery can resize the dock again in that same
// cycle. Coalesce writes into the next frame and leave an unchanged value alone.
const reserveDock = () => {
  if (dockFrame !== null) return;
  dockFrame = requestAnimationFrame(() => {
    dockFrame = null;
    const space = `${Math.ceil(dock.getBoundingClientRect().height) + 40}px`;
    const style = document.documentElement.style;
    if (style.getPropertyValue("--dock-space") !== space) style.setProperty("--dock-space", space);
  });
};
if (typeof ResizeObserver === "function") new ResizeObserver(reserveDock).observe(dock);
else window.addEventListener("resize", reserveDock);
reserveDock();

// These are the only catalog strings that other copy may name. Resolve a
// reference from the raw catalog value, never through t(), so a label cannot
// recurse into itself; call-site variables still win. Keep this map closed:
// allowing arbitrary catalog references would turn copy into a template language.
const UI_REF_KEYS = Object.freeze({
  single_search: "search.single",
  compound_search: "search.compound",
  sources_page: "nav.sources",
});

function uiRefs() {
  const refs = {};
  for (const [name, key] of Object.entries(UI_REF_KEYS)) {
    refs[name] = catalog[key] ?? key;
  }
  return refs;
}

function t(key, vars = {}) {
  const text = catalog[key] ?? key;
  const values = { ...uiRefs(), ...vars };
  // Leave an unresolved token visible rather than hiding a catalog drift as a
  // blank label; tests and rendering checks can then point at the missing value.
  return text.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name] ?? "") : match);
}

function errorText(code, vars = {}) {
  const key = `error.${code}`;
  return t(Object.prototype.hasOwnProperty.call(catalog, key) ? key : "error.source_internal", vars);
}

async function openSourceLink(url) {
  try {
    if (await api().open_link(url) === true) return true;
  } catch { /* The bridge and the OS opener have the same recovery action. */ }
  const dialog = document.getElementById("link-error-dialog");
  document.getElementById("link-error-address").value = String(url);
  if (!dialog.open) dialog.showModal();
  return false;
}

function numberText(value) {
  return Number(value).toLocaleString(document.documentElement?.lang || "en");
}

// Only marked interface copy groups these closed label references. Keep every
// character literal: neither catalog text nor a control label becomes HTML.
function uiCopyRuns(key) {
  const text = catalog[key] ?? key;
  const refs = uiRefs();
  const runs = [];
  let cursor = 0;
  // Quote marks and an attached Korean particle belong to the same inline
  // unit; otherwise the new span introduces a break before the particle.
  for (const match of text.matchAll(/([\p{Ps}\p{Pi}]?)\{(\w+)\}([\p{Pe}\p{Pf}]?)([\p{Script=Hangul}]*)/gu)) {
    if (!Object.prototype.hasOwnProperty.call(UI_REF_KEYS, match[2])) continue;
    if (match.index > cursor) runs.push({ text: text.slice(cursor, match.index), control: false });
    runs.push({ text: match[1] + refs[match[2]] + match[3] + match[4], control: true,
      reference: match[2], prefix: match[1], label: refs[match[2]], suffix: match[3] + match[4] });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) runs.push({ text: text.slice(cursor), control: false });
  return runs;
}

// Catalog text for every marked element under root (the page, or a new panel).
function translate(root) {
  root.querySelectorAll("[data-i18n]").forEach(el => {
    if (!el.hasAttribute("data-ui-copy")) { el.textContent = t(el.dataset.i18n); return; }
    const fragment = document.createDocumentFragment();
    for (const run of uiCopyRuns(el.dataset.i18n)) {
      if (!run.control) { fragment.append(document.createTextNode(run.text)); continue; }
      const label = document.createElement("span");
      label.className = "ui-control-name";
      if (el.dataset.uiCopyAction === "sources" && run.reference === "sources_page") {
        const action = document.createElement("button");
        action.type = "button";
        action.id = "welcome-sources";
        action.className = "inline-view-link";
        action.textContent = run.label;
        action.setAttribute("aria-label", t("sources.choose"));
        action.addEventListener("click", () => showView("sources"));
        label.append(run.prefix, action, run.suffix);
      } else label.textContent = run.text;
      fragment.append(label);
    }
    el.replaceChildren(fragment);
  });
  root.querySelectorAll("[data-i18n-aria]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
  root.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  root.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
}

function applyCatalog() {
  translate(document);
  labelDocuskyOptions();
  updateHint();
  showLibrary();
  updateSource();
  if (currentView === "search") renderAll();
  else resultsNeedTranslation = true;
  updateSelectionNotice();
  syncSearchStatus();
  updateExternalProvider();
  updateExternal();
  updateLanguageList();
  updateSortStatus();
  refreshSourceGuide();
  placeSourceGuide();
}

// The window's in-process bridge to cpe.gui.app.Api (no network involved).
function api() { return window.pywebview.api; }

let languageRequestId = 0;
async function setLanguage(code) {
  const reqId = ++languageRequestId;
  const newCatalog = await api().catalog(code);
  if (reqId !== languageRequestId) return false;
  if (!newCatalog) throw new Error("catalog unavailable");
  catalog = newCatalog;
  currentLanguage = code;
  document.documentElement.lang = code;
  applyCatalog();
  try { await api().set_title(t("app.title")); } catch {}
  return true;
}

const VIEW_NAMES = ["search", "sources", "import", "language"];
let currentView = "search";
let resultsNeedTranslation = false;
function viewElement(name) { return document.getElementById(`view-${name}`); }
function showView(name, { focus = true } = {}) {
  if (!VIEW_NAMES.includes(name)) return;
  // A native dialog owns Escape and focus while it is open. Do not move the
  // screen underneath it.
  if (document.querySelector("dialog[open]") && name !== currentView) return;
  if (name !== "sources" && typeof closeSourceGuide === "function") closeSourceGuide({ restoreFocus: false });
  currentView = name;
  VIEW_NAMES.forEach(code => {
    viewElement(code)?.classList.toggle("is-hidden", code !== name);
  });
  document.querySelectorAll(".top-nav [data-view]").forEach(button => {
    const active = button.dataset.view === name;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (name === "search" && resultsNeedTranslation) { renderAll(); resultsNeedTranslation = false; }
  if (name === "sources") autoLoadSavedSlots();
  if (name === "import") {
    showImportProviders(false);
    refreshHistoryAvailability();
  }
  if (name === "language") updateLanguageList();
  if (!focus) return;
  if (name === "search") {
    const currentMode = document.querySelector('input[name=mode]:checked')?.value;
    document.getElementById(currentMode === "words" ? "query" : "first")?.focus();
  } else {
    viewElement(name)?.querySelector("h2")?.focus();
  }
}
document.addEventListener("keydown", event => {
  // IME cancellation belongs to the input, and a held Escape must not traverse
  // several screens. A child control may already own this key as well.
  if (event.isComposing || event.keyCode === 229 || event.repeat || event.defaultPrevented) return;
  if (event.key !== "Escape" || document.querySelector("dialog[open]")) return;
  if (currentView === "sources" && closeSourceGuide()) {
    event.preventDefault();
    return;
  }
  if (currentView === "import" && !document.getElementById("import-providers").classList.contains("is-hidden")) {
    event.preventDefault();
    showImportProviders(false);
    return;
  }
  if (currentView !== "search") {
    event.preventDefault();
    showView("search");
  }
});
function showImportProviders(open) {
  document.getElementById("import-providers").classList.toggle("is-hidden", !open);
  document.querySelector("#view-import .import-menu").classList.toggle("is-hidden", open);
  if (open) document.getElementById("import-providers-title")?.focus();
  else document.getElementById("import-originals")?.focus();
}

let library = { text_count: 0, folder: "", skipped: [], skipped_count: 0 };
let localState = { active: "", busy: false, selected: [], libraries: [] };
let languages = [];
let currentLanguage = "en";
let localBusy = false;
let savingSources = false;
const LOCAL_SOURCES = ["local", "local2", "local3", "local4"];
function localInfo(code) { return localState.libraries.find(item => item.id === code); }
// text_count is what the bridge sends now; the fallback keeps older fixtures and
// cached page state readable while a folder is being replaced.
function textCount(info) { return info?.text_count ?? info?.texts?.length ?? 0; }
function skippedCount(info) { return info?.skipped_count ?? info?.skipped?.length ?? 0; }
function activeLocalReady() {
  const info = localInfo(localState.active);
  return Boolean(info?.configured && !info.load_error);
}
const autoLoadTried = new Set();
const loadingSlots = new Set();
const localBusyTasks = new Set();
function beginLocalBusy(task = Symbol()) {
  const token = typeof task === "string" ? Symbol(task) : task;
  localBusyTasks.add(token);
  localBusy = true;
  updateSource();
  return token;
}
function endLocalBusy(task) {
  localBusyTasks.delete(task);
  localBusy = localBusyTasks.size > 0;
  updateSource();
}
function isLocalBusy() {
  return localBusyTasks.size > 0 || (typeof localBusy !== "undefined" && Boolean(localBusy));
}
function folderParent(path) {
  const parts = String(path || "").split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 2] : "";
}
function localLabel(code) {
  const info = localInfo(code);
  const fallback = t("local.slot", { n: LOCAL_SOURCES.indexOf(code) + 1 });
  if (!info?.configured) return fallback;
  const name = info.name || fallback;
  const sameName = localState.libraries.filter(item => item.configured && item.name === info.name);
  if (sameName.length < 2) return name;
  const parent = folderParent(info.folder);
  return parent ? `${name} · ${parent}` : name;
}
function sourceName(code) {
  if (LOCAL_SOURCES.includes(code)) return localLabel(code);
  if (code.startsWith("docusky:")) {
    return `${code.slice("docusky:".length)} (${t("source.docusky.unreviewed")})`;
  }
  return t(`source.${code}`);
}
function showLocalControls() {
  const busy = isLocalBusy() || localState.busy || savingSources;
  localRows.forEach(({ code, row, title, path, detail, load, choose, remove, skipped, skippedText }) => {
    const info = localInfo(code);
    row.classList.toggle("is-hidden", !info?.configured);
    title.textContent = localLabel(code);
    path.textContent = info?.folder || t("local.unset");
    detail.textContent = info?.load_error
      ? errorText(info.load_error, { path: info.folder })
      : info?.configured
        ? info.loaded === false
          ? loadingSlots.has(code)
            ? t("sources.loading")
            : t("local.not_loaded")
          : t("local.count", {
            n: numberText(textCount(info)),
            skipped: numberText(skippedCount(info)),
          })
        : "";
    detail.classList.toggle("is-error", Boolean(info?.load_error));
    const canRetry = Boolean(
      info?.load_error
      || (info?.configured && info.loaded === false && autoLoadTried.has(code) && !loadingSlots.has(code)),
    );
    const canReload = Boolean(info?.loaded && info.folder && !info.load_error);
    load.textContent = t(loadingSlots.has(code) ? "sources.loading" : canReload ? "local.reload" : info?.load_error ? "sources.retry" : "local.load");
    load.classList.toggle("is-hidden", !canRetry && !canReload);
    load.title = t(canReload ? "local.reload_hint" : "local.load");
    load.setAttribute("aria-label", t("local.folder_action", { name: localLabel(code), action: load.textContent }));
    load.disabled = busy || loadingSlots.has(code);
    choose.textContent = t(info?.folder ? "local.change" : "library.open");
    choose.setAttribute("aria-label", t("local.folder_action", { name: localLabel(code), action: choose.textContent }));
    choose.disabled = busy;
    remove.disabled = busy || loadingSlots.has(code);
    const skippedCountValue = skippedCount(info);
    const files = (info?.skipped || []).slice(0, 5);
    const issue = JSON.stringify([info?.folder, skippedCountValue, files]);
    skipped.classList.toggle("is-hidden", !skippedCountValue);
    if (skippedCountValue && skipped.dataset.issue !== issue) skipped.open = true;
    skipped.dataset.issue = issue;
    skippedText.textContent = skippedCountValue
      ? t("library.skipped", {
        n: numberText(skippedCountValue),
        files: files.join(", ") + (skippedCountValue > 5 ? " …" : ""),
      })
      : "";
  });
  const add = document.getElementById("add-local-folder");
  add.classList.toggle("is-hidden", !LOCAL_SOURCES.some(code => !localInfo(code)?.configured));
  add.disabled = busy;
  document.getElementById("local-empty").classList.toggle(
    "is-hidden",
    LOCAL_SOURCES.some(code => localInfo(code)?.configured),
  );
  const target = document.getElementById("local-import-target");
  const options = localState.libraries
    .filter(item => item.configured && !item.load_error)
    .map(item => new Option(sourceName(item.id), item.id));
  if (!options.some(option => option.value === localState.active)) {
    const info = localInfo(localState.active);
    const placeholder = new Option(
      info?.configured
        ? t("local.target_unreadable", { name: sourceName(localState.active) })
        : t("local.unset"),
      localState.active,
    );
    placeholder.disabled = true;
    options.unshift(placeholder);
  }
  target.replaceChildren(...options);
  target.value = localState.active;
  target.disabled = busy;
  const importSummaryName = document.getElementById("sources-import-target-name");
  if (importSummaryName) {
    const optionsList = target.options ? Array.from(target.options) : (target.children || []);
    const selectedOption = (target.selectedIndex >= 0 && target.options ? target.options[target.selectedIndex] : null)
      || optionsList.find(opt => opt.value === target.value)
      || optionsList[0];
    importSummaryName.textContent = selectedOption ? (selectedOption.text || selectedOption.textContent || "") : t("local.unset");
  }
  ["ctp-open", "release-open"].forEach(id => {
    const button = document.getElementById(id);
    if (button) button.disabled = busy;
  });
  document.querySelectorAll("[data-external-provider]").forEach(button => {
    button.disabled = busy;
  });
}
async function autoLoadSavedSlots() {
  if (!localState.libraries.length) return;
  for (const info of [...localState.libraries]) {
    if (!info.configured || info.loaded || info.load_error || autoLoadTried.has(info.id)) continue;
    await loadLocalSlot(info.id, { auto: true });
  }
}
const removeDialog = document.getElementById("remove-dialog");
let pendingRemove = "";
function askRemoveLocal(code) {
  pendingRemove = code;
  document.getElementById("remove-message").textContent = t(
    "sources.remove_confirm_named",
    { name: localLabel(code) },
  );
  removeDialog.showModal();
}
document.getElementById("remove-confirm").addEventListener("click", async () => {
  const code = pendingRemove;
  pendingRemove = "";
  removeDialog.close();
  // The removed row held focus, so native dialog restoration has nowhere to
  // land. Move to the view heading only after a successful removal; a failed
  // removal leaves the row and its focus alone.
  if (await removeLocal(code)) {
    requestAnimationFrame(() => document.getElementById("sources-title").focus());
  }
});
removeDialog.addEventListener("close", () => { pendingRemove = ""; });
async function removeLocal(code) {
  const task = beginLocalBusy();
  try {
    const answer = await api().remove_local(code);
    if (answer.error) { localError(answer); return false; }
    localState = answer;
    autoLoadTried.delete(code);
    loadingSlots.delete(code);
    library = await api().library();
    updateSource();
    clearResults();
    refreshHistoryAvailability();
    return true;
  } catch { localError({ error: "network" }); return false; }
  finally { endLocalBusy(task); }
}
async function ensureTargetFolder(statusId = "status") {
  if (activeLocalReady()) return true;
  const task = beginLocalBusy();
  const status = document.getElementById(statusId);
  if (status) status.textContent = "";
  try {
    const answer = await api().choose_folder("");
    if (answer.cancelled) {
      if (status) status.textContent = t("error.folder_not_chosen");
      return false;
    }
    if (answer.error) { localError(answer, statusId); return false; }
    await acceptLibrary(answer);
    const box = localRows.find(row => row.code === localState.active)?.box;
    if (box && sources().length < MAX_SOURCES) box.checked = true;
    clearResults();
    await saveSources();
    return true;
  } catch {
    localError({ error: "network" }, statusId);
    return false;
  } finally {
    endLocalBusy(task);
  }
}
async function acceptLibrary(answer) {
  library = answer;
  try { localState = await api().local_libraries(); } catch {}
  showLibrary(); updateSource();
  refreshHistoryAvailability();
}
async function refreshFailedImport(error) {
  if (!["load_failed", "preferences_write_failed"].includes(error)) return;
  try { localState = await api().local_libraries(); } catch {}
  clearResults();
  updateSource();
}
function localError(answer, statusId = "local-status") {
  const status = document.getElementById(statusId);
  status.textContent = errorText(answer.error, answer);
  const section = status.closest("details");
  if (section) section.open = true;
  status.scrollIntoView({ block: "nearest" });
}
function settingsError(answer) {
  const status = document.getElementById("settings-status");
  status.textContent = errorText(answer.error, answer);
}
async function saveSources() {
  if (savingSources) return;
  savingSources = true; updateSource();
  try {
    const answer = await api().set_sources(sources(), {
      historydb_site: historydbSite,
      historydb_item: historydbItem,
      nahf_item: nahfItem,
    });
    if (answer.error) { localError(answer); restoreSources(); }
    else localState.selected = answer.sources;
  } catch { localError({ error: "network" }); restoreSources(); }
  finally { savingSources = false; updateSource(); }
}
function restoreSources() {
  const saved = localState.selected.find(code => code.startsWith("docusky:"));
  if (saved) {
    if (![...docuskySelect.options].some(option => option.value === saved)) {
      docuskyOption(saved, saved.slice("docusky:".length));
    }
    docuskySelect.value = saved;
    docuskyBox.value = saved;
  }
  allSourceBoxes().forEach(box => { box.checked = localState.selected.includes(box.value); });
  docuskyProfiles.classList.toggle("is-hidden", !docuskyBox.checked);
  // Checking the box here fires no change event, so the database list is asked
  // for now; otherwise the dropdown keeps only the reviewed database until the
  // researcher toggles the chip.
  if (docuskyBox.checked && !docuskyCatalogLoaded) {
    docuskyCatalogLoaded = true;
    loadDocuskyDatabases();
  }
}
async function chooseLocalFolder(code = "", statusId = "local-status") {
  const task = beginLocalBusy();
  document.getElementById(statusId).textContent = "";
  try {
    const answer = await api().choose_folder(code);
    if (answer.cancelled) return;
    if (answer.error) { localError(answer, statusId); return; }
    await acceptLibrary(answer);
    const box = localRows.find(row => row.code === localState.active)?.box;
    if (box && sources().length < MAX_SOURCES) box.checked = true;
    clearResults(); await saveSources();
  } catch { localError({ error: "network" }, statusId); }
  finally { endLocalBusy(task); }
}
async function loadLocalSlot(code, { auto = false } = {}) {
  if (loadingSlots.has(code)) return;
  const info = localInfo(code);
  const reload = !auto && info?.loaded && info.folder && !info.load_error;
  autoLoadTried.add(code);
  if (!auto) autoLoadTried.delete(code);
  loadingSlots.add(code);
  const task = beginLocalBusy();
  document.getElementById("local-status").textContent = "";
  try {
    const answer = reload ? await api().reload_local(code) : await api().load_local(code);
    if (answer.error) {
      localError(answer);
      try { localState = await api().local_libraries(); } catch {}
    } else {
      localState = answer;
      if (code === answer.active) library = await api().library();
      clearResults();
      // A saved slot that finishes loading can make the selection usable; the
      // reason shown for an earlier submit must not outlive it.
      syncSourceChoiceReason();
    }
  } catch { localError({ error: "network" }); }
  finally {
    loadingSlots.delete(code);
    endLocalBusy(task);
  }
}
function showLibrary() {
  showLocalControls();
  updateWelcome();
}

document.getElementById("local-import-target").addEventListener("change", async event => {
  const code = event.target.value;
  const task = beginLocalBusy();
  document.getElementById("settings-status").textContent = "";
  try {
    const answer = await api().activate_local(code);
    if (answer.error) {
      settingsError(answer);
      try { localState = await api().local_libraries(); } catch {}
      return;
    }
    await acceptLibrary(answer);
  } catch { settingsError({ error: "network" }); }
  finally { endLocalBusy(task); }
});

const localImportTarget = document.getElementById("local-import-target");
const openTargetFolderChooser = event => {
  const hasConfigured = localState.libraries.some(item => item.configured && !item.load_error);
  if (!hasConfigured && !isLocalBusy()) {
    if (event) event.preventDefault();
    chooseLocalFolder("", "settings-status");
  }
};
localImportTarget.addEventListener("pointerdown", event => {
  const hasConfigured = localState.libraries.some(item => item.configured && !item.load_error);
  if (!hasConfigured) {
    event.preventDefault();
    openTargetFolderChooser(event);
  }
});
localImportTarget.addEventListener("click", event => {
  const hasConfigured = localState.libraries.some(item => item.configured && !item.load_error);
  if (!hasConfigured) {
    event.preventDefault();
    openTargetFolderChooser(event);
  }
});
localImportTarget.addEventListener("keydown", event => {
  const hasConfigured = localState.libraries.some(item => item.configured && !item.load_error);
  if (!hasConfigured && (event.key === "Enter" || event.key === " " || event.key === "ArrowDown")) {
    event.preventDefault();
    openTargetFolderChooser(event);
  }
});

// Import a supplied CTP address through cpe.ctp.adapter and search the saved text
// locally. A missing key does not block requests; the provider decides access.
const ctpDialog = document.getElementById("ctp-dialog");
async function showCtpKeyState() {
  const stateEl = document.getElementById("ctp-key-state");
  const status = document.getElementById("ctp-status");
  try {
    const isSet = await api().ctp_key_set();
    stateEl.textContent = t(isSet ? "ctp.key.stored" : "ctp.key.missing");
  } catch {
    stateEl.textContent = t("ctp.key.missing");
    if (status) {
      status.classList.add("is-error");
      status.textContent = t("error.network");
    }
  }
}
document.getElementById("ctp-open").addEventListener("click", async () => {
  const status = document.getElementById("ctp-status");
  status.classList.remove("is-error");
  status.textContent = "";
  ctpDialog.showModal();
  document.getElementById("ctp-url").focus();
  try {
    await showCtpKeyState();
  } catch {
    status.classList.add("is-error");
    status.textContent = t("error.network");
  }
});
// A single Enter key inside these non-form address fields runs the same action as
// the visible button. IME composition, held keys and disabled buttons stay inert.
function activateOnEnter(inputId, buttonId) {
  const input = document.getElementById(inputId);
  const button = document.getElementById(buttonId);
  if (!input || !button) return;
  input.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229 || event.repeat) return;
    event.preventDefault();
    button.click();
  });
}
activateOnEnter("ctp-url", "ctp-import");
activateOnEnter("external-address", "external-fetch");

document.getElementById("ctp-key-save").addEventListener("click", async () => {
  const input = document.getElementById("ctp-key");
  const status = document.getElementById("ctp-status");
  status.classList.remove("is-error");
  status.textContent = "";
  try {
    const saved = await api().save_ctp_key(input.value);
    if (!saved) {
      status.classList.add("is-error");
      status.textContent = t("error.preferences_write_failed");
      return;
    }
    input.value = "";
    await showCtpKeyState();
  } catch {
    status.classList.add("is-error");
    status.textContent = t("error.network");
  }
});
let ctpBusy = false;
ctpDialog.addEventListener("cancel", event => { if (ctpBusy) event.preventDefault(); });
document.getElementById("ctp-import").addEventListener("click", async () => {
  if (!await ensureTargetFolder("ctp-status")) return;
  const status = document.getElementById("ctp-status");
  const button = document.getElementById("ctp-import");
  const close = ctpDialog.querySelector(".dialog-close");
  ctpBusy = true;
  const task = beginLocalBusy();
  button.disabled = true;
  close.disabled = true;
  ctpDialog.setAttribute("aria-busy", "true");
  status.classList.remove("is-error");
  status.textContent = t("ctp.working");
  try {
    const answer = await api().import_ctp(document.getElementById("ctp-url").value);
    if (answer.error) {
      status.classList.add("is-error");
      status.textContent = errorText(answer.error, answer);
    } else {
      if (answer.library?.error) {
        await refreshFailedImport(answer.library.error);
        status.classList.add("is-error");
        status.textContent = t("ctp.done", answer.imported) + " " + t("import.reload_failed") + " " + errorText(answer.library.error, answer.library);
      } else if (answer.library) {
        status.textContent = t("ctp.done", answer.imported);
        await acceptLibrary(answer.library);
        clearResults();
      }
    }
  } catch {
    status.classList.add("is-error");
    status.textContent = t("error.network");
  } finally {
    ctpBusy = false;
    endLocalBusy(task);
    button.disabled = false;
    close.disabled = false;
    ctpDialog.removeAttribute("aria-busy");
  }
});

// Preview tokens identify downloaded text; job IDs identify cancellable work.
const externalDialog = document.getElementById("external-dialog");
const external = { token: "", selected: new Set(), units: [], report: false, busy: false, view: 0, job: "", cancelling: false };
const riseState = {
  booksPage: 1,
  booksTotalPages: 1,
  booksTotal: 0,
  books: [],
  selectedBook: "",
  sections: [],
  selectedSection: "",
  range: 1,
  totalUnits: null,
  totalRanges: 1,
  loadingBooks: false,
  loadingSections: false,
  booksReqId: 0,
  sectionsReqId: 0,
  alert: "",
};
let riseRetryAction = null;
function showRiseError(answer, retryCallback) {
  const stateEl = document.getElementById("external-rise-state");
  const errEl = document.getElementById("external-rise-error");
  const retryBtn = document.getElementById("external-rise-retry");
  if (!stateEl || !errEl) return;
  errEl.textContent = errorText(answer.error, answer);
  if (retryBtn) {
    retryBtn.classList.remove("is-hidden");
    riseRetryAction = retryCallback;
  }
  stateEl.classList.remove("is-hidden");
  riseState.alert = "error";
}
function showRiseEmpty(i18nKey) {
  const stateEl = document.getElementById("external-rise-state");
  const errEl = document.getElementById("external-rise-error");
  const retryBtn = document.getElementById("external-rise-retry");
  if (!stateEl || !errEl) return;
  errEl.textContent = t(i18nKey);
  if (retryBtn) retryBtn.classList.add("is-hidden");
  stateEl.classList.remove("is-hidden");
  const status = document.getElementById("external-status");
  if (status) {
    status.classList.remove("is-error");
    status.textContent = "";
  }
  riseState.alert = "empty";
  riseRetryAction = null;
}
function hideRiseError() {
  const stateEl = document.getElementById("external-rise-state");
  const errEl = document.getElementById("external-rise-error");
  if (stateEl) stateEl.classList.add("is-hidden");
  // A successful retry must not leave the previous message in the hidden
  // error element; the next failure sets its own text via showRiseError.
  if (errEl) errEl.textContent = "";
  const status = document.getElementById("external-status");
  if (status) {
    status.classList.remove("is-error");
    status.textContent = "";
  }
  riseState.alert = "";
  riseRetryAction = null;
}
function syncRiseAddress() {
  const address = document.getElementById("external-address");
  if (riseState.selectedBook && riseState.selectedSection) {
    address.value = `${riseState.selectedBook}/${riseState.selectedSection}/${riseState.range}`;
  } else {
    address.value = "";
  }
  const prevBtn = document.getElementById("external-rise-range-prev");
  const nextBtn = document.getElementById("external-rise-range-next");
  const label = document.getElementById("external-rise-range-label");
  if (prevBtn) prevBtn.disabled = external.busy || riseState.range <= 1;
  if (nextBtn) nextBtn.disabled = external.busy || riseState.totalUnits === null || riseState.range >= riseState.totalRanges;
  if (label) {
    label.textContent = riseState.totalUnits !== null
      ? t("external.rise.range_display_total", { range: riseState.range, total: riseState.totalRanges })
      : t("external.rise.range_display", { range: riseState.range });
  }
  updateExternal();
}
async function loadRiseBooks(page = 1) {
  const reqId = ++riseState.booksReqId;
  riseState.sectionsReqId++;
  riseState.loadingSections = false;
  clearExternalPreview();
  riseState.loadingBooks = true;
  riseState.booksPage = page;
  riseState.books = [];
  riseState.selectedBook = "";
  riseState.sections = [];
  riseState.selectedSection = "";
  riseState.range = 1;
  riseState.totalUnits = null;
  riseState.totalRanges = 1;
  syncRiseAddress();
  hideRiseError();
  const bookSelect = document.getElementById("external-rise-book");
  const prevBtn = document.getElementById("external-rise-book-prev");
  const nextBtn = document.getElementById("external-rise-book-next");
  const pageLabel = document.getElementById("external-rise-book-page");
  if (bookSelect) {
    bookSelect.disabled = true;
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = t("external.rise.books_loading");
    bookSelect.replaceChildren(opt);
  }
  if (prevBtn) prevBtn.disabled = true;
  if (nextBtn) nextBtn.disabled = true;
  if (pageLabel) pageLabel.textContent = "";
  updateExternal();
  try {
    const res = await api().rise_books(page);
    if (reqId !== riseState.booksReqId) return;
    riseState.loadingBooks = false;
    if (res.error) {
      externalError(res);
      if (bookSelect) bookSelect.replaceChildren();
      showRiseError(res, () => loadRiseBooks(page));
      updateExternal();
      return;
    }
    hideRiseError();
    riseState.books = res.books || [];
    riseState.booksPage = res.page || page;
    riseState.booksTotal = res.total || 0;
    const perPage = res.per_page || 50;
    riseState.booksTotalPages = Math.max(1, Math.ceil(riseState.booksTotal / perPage));
    if (bookSelect) {
      bookSelect.replaceChildren(...riseState.books.map(b => {
        const opt = document.createElement("option");
        opt.value = b.uuid;
        opt.textContent = b.name;
        return opt;
      }));
    }
    if (pageLabel) {
      pageLabel.textContent = t("external.rise.books_page", {
        page: riseState.booksPage,
        pages: riseState.booksTotalPages,
        total: riseState.booksTotal,
      });
    }
    if (riseState.books.length > 0) {
      riseState.selectedBook = riseState.books[0].uuid;
      if (bookSelect) bookSelect.value = riseState.selectedBook;
      await loadRiseSections(riseState.selectedBook);
    } else {
      riseState.selectedBook = "";
      riseState.sections = [];
      riseState.selectedSection = "";
      const secSelect = document.getElementById("external-rise-section");
      if (secSelect) secSelect.replaceChildren();
      showRiseEmpty("external.rise.empty_books");
      syncRiseAddress();
    }
  } catch {
    if (reqId !== riseState.booksReqId) return;
    riseState.loadingBooks = false;
    externalError({ error: "external_network" });
    showRiseError({ error: "external_network" }, () => loadRiseBooks(page));
  } finally {
    if (reqId === riseState.booksReqId) {
      updateExternal();
    }
  }
}
async function loadRiseSections(bookUuid) {
  const reqId = ++riseState.sectionsReqId;
  clearExternalPreview();
  riseState.loadingSections = true;
  riseState.selectedBook = bookUuid;
  riseState.sections = [];
  riseState.selectedSection = "";
  riseState.range = 1;
  riseState.totalUnits = null;
  riseState.totalRanges = 1;
  syncRiseAddress();
  hideRiseError();
  const sectionSelect = document.getElementById("external-rise-section");
  if (sectionSelect) {
    sectionSelect.disabled = true;
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = t("external.rise.sections_loading");
    sectionSelect.replaceChildren(opt);
  }
  updateExternal();
  try {
    const res = await api().rise_sections(bookUuid);
    if (reqId !== riseState.sectionsReqId) return;
    riseState.loadingSections = false;
    if (res.error) {
      externalError(res);
      riseState.sections = [];
      riseState.selectedSection = "";
      if (sectionSelect) sectionSelect.replaceChildren();
      showRiseError(res, () => loadRiseSections(bookUuid));
      syncRiseAddress();
      updateExternal();
      return;
    }
    hideRiseError();
    riseState.sections = res.sections || [];
    if (sectionSelect) {
      sectionSelect.replaceChildren(...riseState.sections.map(s => {
        const opt = document.createElement("option");
        opt.value = s.uuid;
        opt.textContent = s.name;
        return opt;
      }));
    }
    if (riseState.sections.length > 0) {
      riseState.selectedSection = riseState.sections[0].uuid;
      if (sectionSelect) sectionSelect.value = riseState.selectedSection;
    } else {
      riseState.selectedSection = "";
      showRiseEmpty("external.rise.empty_sections");
    }
    syncRiseAddress();
  } catch {
    if (reqId !== riseState.sectionsReqId) return;
    riseState.loadingSections = false;
    riseState.sections = [];
    riseState.selectedSection = "";
    syncRiseAddress();
    externalError({ error: "external_network" });
    showRiseError({ error: "external_network" }, () => loadRiseSections(bookUuid));
  } finally {
    if (reqId === riseState.sectionsReqId) {
      updateExternal();
    }
  }
}
function updateExternal() {
  externalDialog.setAttribute("aria-busy", String(external.busy));
  const provider = document.getElementById("external-provider").value;
  const offlineFile = document.getElementById("external-file-name");
  const offline = ["cbeta", "nikh-partial"].includes(provider);
  const rise = provider === "rise";
  offlineFile.textContent = offline ? document.getElementById("external-address").value : "";
  externalDialog.querySelector(".dialog-close").disabled = external.busy;
  document.getElementById("external-selection").classList.toggle("is-hidden", !external.units.length);
  document.getElementById("external-selected").textContent = t("external.selected", { n: external.selected.size });
  document.getElementById("external-include").checked = external.selected.has(document.getElementById("external-unit").value);
  document.getElementById("external-import").disabled = external.busy || !external.token || !external.selected.size || !document.getElementById("external-reviewed").checked || !document.getElementById("external-edition").value.trim();
  ["external-fetch", "external-provider", "external-address", "external-unit", "external-include", "external-all", "external-reviewed", "external-edition", "external-from", "external-to", "external-range-select", "external-file", "external-revision"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = external.busy;
  });
  ["external-unit", "external-include", "external-all", "external-reviewed"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = external.busy || !external.units.length;
  });
  if (rise) {
    const bookSelect = document.getElementById("external-rise-book");
    const bookPrev = document.getElementById("external-rise-book-prev");
    const bookNext = document.getElementById("external-rise-book-next");
    const secSelect = document.getElementById("external-rise-section");
    const rangePrev = document.getElementById("external-rise-range-prev");
    const rangeNext = document.getElementById("external-rise-range-next");
    if (bookSelect) bookSelect.disabled = external.busy || riseState.loadingBooks;
    if (bookPrev) bookPrev.disabled = external.busy || riseState.loadingBooks || riseState.booksPage <= 1;
    if (bookNext) bookNext.disabled = external.busy || riseState.loadingBooks || riseState.booksPage >= riseState.booksTotalPages;
    if (secSelect) secSelect.disabled = external.busy || riseState.loadingSections || !riseState.sections.length;
    if (rangePrev) rangePrev.disabled = external.busy || riseState.range <= 1;
    if (rangeNext) rangeNext.disabled = external.busy || riseState.totalUnits === null || riseState.range >= riseState.totalRanges;
    const fetchBtn = document.getElementById("external-fetch");
    if (fetchBtn) fetchBtn.disabled = external.busy || riseState.loadingBooks || riseState.loadingSections || !riseState.selectedSection;
  }
  document.getElementById("external-report").disabled = external.busy || !external.token;
  document.getElementById("external-report").classList.toggle("is-hidden", !external.report);
  document.getElementById("external-cancel").classList.toggle("is-hidden", !external.job);
  document.getElementById("external-cancel").disabled = external.cancelling;
  document.getElementById("external-progress").classList.toggle("is-hidden", !external.job);
  // The final action stays visible but disabled until a preview with units,
  // a selection, an edition and a review check are all in place.
  const fetchBtn = document.getElementById("external-fetch");
  const importBtn = document.getElementById("external-import");
  const hasPreview = Boolean(external.token) && external.units.length > 0;
  // The final action is always visible; its label names the actual operation:
  // online sources download, offline files import.
  if (importBtn) importBtn.textContent = t(offline ? "external.import" : "external.download");
  // The fetch action stays a secondary preview control in every step.
  // Fetch stays disabled while its required input is missing, with the reason
  // spelled out in the next-step line below instead of color alone. CBETA
  // also needs a valid 40 lowercase-hex release code; NIKH files do not.
  if (fetchBtn && !rise && !external.busy) {
    if (!document.getElementById("external-address").value.trim()) fetchBtn.disabled = true;
    // Same 40 lowercase-hex rule the backend enforces; anything else would
    // fail with external_revision after the wait.
    if (provider === "cbeta" && !/^[a-f0-9]{40}$/.test(document.getElementById("external-revision").value.trim())) fetchBtn.disabled = true;
  }
  updateExternalNextStep({ provider, offline, rise, hasPreview });
}
function updateExternalNextStep({ provider, offline, rise, hasPreview }) {
  const next = document.getElementById("external-next-step");
  if (!next) return;
  // The rise block reports its own errors and empty states; do not talk over it.
  if (rise && riseState.alert) { next.textContent = ""; return; }
  if (external.busy) { next.textContent = ""; return; }
  if (rise) {
    if (riseState.loadingBooks || riseState.loadingSections || !riseState.selectedSection) {
      next.textContent = t("external.next_rise");
      return;
    }
  } else if (!document.getElementById("external-address").value.trim()) {
    next.textContent = t(offline ? "external.next_file" : "external.next_address");
    return;
  }
  if (provider === "cbeta" && !/^[a-f0-9]{40}$/.test(document.getElementById("external-revision").value.trim())) {
    next.textContent = t("external.next_revision");
    return;
  }
  if (!external.token) {
    next.textContent = t(offline ? "external.next_check" : "external.next_preview");
    return;
  }
  if (!hasPreview) { next.textContent = t("external.next_excluded"); return; }
  if (!external.selected.size) { next.textContent = t("external.next_select"); return; }
  if (!document.getElementById("external-edition").value.trim()) { next.textContent = t("external.next_edition"); return; }
  if (!document.getElementById("external-reviewed").checked) { next.textContent = t("external.next_review"); return; }
  const action = t(offline ? "external.import" : "external.download");
  const folder = importTargetName();
  next.textContent = folder
    ? t("external.next_ready", { folder, action })
    : t("external.next_ready_nofolder", { action });
}
function importTargetName() {
  const target = document.getElementById("local-import-target");
  if (!target || !target.value) return "";
  const options = target.options || [];
  const option = options[target.selectedIndex >= 0 ? target.selectedIndex : 0];
  if (!option || option.disabled) return "";
  return option.text || option.textContent || "";
}
function updateExternalProvider() {
  const provider = document.getElementById("external-provider").value;
  const offline = provider === "cbeta" || provider === "nikh-partial";
  const ndl = provider === "ndl";
  const rise = provider === "rise";
  const address = document.getElementById("external-address");
  const addressField = document.getElementById("external-address-field");
  const fileField = document.getElementById("external-file-field");
  const riseFields = document.getElementById("external-rise-fields");
  document.getElementById("external-address-label").textContent = t(
    ndl ? "external.ndl.address" : "external.gutenberg.address",
  );
  address.placeholder = t(ndl ? "external.ndl.placeholder" : "external.gutenberg.placeholder");
  addressField.classList.toggle("is-hidden", offline || rise);
  fileField.classList.toggle("is-hidden", !offline);
  if (riseFields) riseFields.classList.toggle("is-hidden", !rise);
  document.getElementById("external-find").classList.toggle("is-hidden", offline || rise);
  const findLink = document.getElementById("external-find-link");
  if (findLink) {
    findLink.href = ndl ? "https://dl.ndl.go.jp/search/" : "https://www.gutenberg.org/ebooks/";
    findLink.textContent = t(ndl ? "external.find_ndl" : "external.find_gutenberg");
  }
  document.getElementById("external-fetch").textContent = t(
    offline ? "external.read_file" : "external.fetch",
  );
  document.getElementById("external-help").textContent = t(
    rise ? "external.rise.help" : offline ? "external.help" : ndl ? "external.ndl.help" : "external.gutenberg.help",
  );
  document.getElementById("external-offline-help").classList.toggle("is-hidden", !offline);
  document.getElementById("external-revision-field").classList.toggle("is-hidden", provider !== "cbeta");
  document.getElementById("external-revision-help").classList.toggle("is-hidden", provider !== "cbeta");
  const revisionFind = document.getElementById("external-revision-find");
  if (revisionFind) revisionFind.classList.toggle("is-hidden", provider !== "cbeta");
  address.readOnly = offline || rise;
  document.getElementById("external-evidence").textContent = t(
    rise ? "external.rise.evidence" : offline ? "external.offline.evidence" : ndl ? "external.evidence" : "external.gutenberg.evidence",
  );
  if (rise && (externalDialog.open || externalDialog.open === undefined)) {
    if (!riseState.books.length && !riseState.loadingBooks) {
      loadRiseBooks(1);
    } else if (!riseState.selectedBook || !riseState.sections.length || !riseState.selectedSection) {
      if (!riseState.loadingSections) {
        if (!riseState.selectedBook && riseState.books.length) {
          riseState.selectedBook = riseState.books[0].uuid;
          const bookSelect = document.getElementById("external-rise-book");
          if (bookSelect) bookSelect.value = riseState.selectedBook;
        }
        if (riseState.selectedBook) {
          loadRiseSections(riseState.selectedBook);
        }
      }
    } else {
      syncRiseAddress();
      if (riseState.booksTotal > 0) {
        const pageLabel = document.getElementById("external-rise-book-page");
        if (pageLabel) {
          pageLabel.textContent = t("external.rise.books_page", {
            page: riseState.booksPage,
            pages: riseState.booksTotalPages,
            total: riseState.booksTotal,
          });
        }
      }
    }
  }
}
function showExternalExclusions(details = {}) {
  const excluded = details.excluded || [];
  document.getElementById("external-excluded-summary").textContent = details.policy ? t("external.excluded", { n: excluded.length }) : "";
  const reasons = { inline_apparatus_or_note: "external.skip.inline", unsupported_inline: "external.skip.unsupported", nonprose_or_unsupported_container: "external.skip.container", text_outside_units: "external.skip.unstructured", text_outside_paragraphs: "external.skip.unstructured", proofreading_policy: "external.skip.proofreading", original_note_structure: "external.skip.note", mixed_script_or_placeholder_review: "external.skip.mixed", new_character_text: "external.skip.glyph", new_character_title: "external.skip.glyph", new_character_date: "external.skip.glyph", unresolved_glyph: "external.skip.unresolved_glyph", back_matter_unreviewed: "external.skip.back" };
  document.getElementById("external-excluded").replaceChildren(...excluded.map(item => {
    const li = document.createElement("li");
    li.textContent = item.id + " · " + t(reasons[item.reason] || "external.skip.unsupported"); return li;
  }));
  const availability = document.getElementById("external-availability");
  availability.value = details.availability || "";
  availability.classList.toggle("is-hidden", !details.availability);
}
function externalError(answer) {
  const status = document.getElementById("external-status");
  status.classList.add("is-error");
  status.textContent = errorText(answer.error, answer);
}
function clearExternalPreview() {
  const oldToken = external.token;
  external.token = ""; external.selected.clear(); external.units = []; external.report = false; external.view++;
  document.getElementById("external-preview").classList.add("is-hidden");
  document.getElementById("external-text").value = "";
  const cov = document.getElementById("external-coverage");
  if (cov) cov.textContent = "";
  document.getElementById("external-source").removeAttribute("href");
  document.getElementById("external-reviewed").checked = false;
  if (oldToken) api().discard_external(oldToken)?.catch?.(() => {});
  updateExternal();
}
async function showExternalUnit() {
  if (!external.units.length) return;
  const view = ++external.view;
  document.getElementById("external-text").value = "";
  document.getElementById("external-source").removeAttribute("href");
  try {
    const answer = await api().external_unit(external.token, document.getElementById("external-unit").value);
    if (view !== external.view) return;
    if (answer.error) { externalError(answer); return; }
    document.getElementById("external-text").value = answer.text;
    document.getElementById("external-source").href = answer.source;
    updateExternal();
  } catch {
    if (view !== external.view) return;
    externalError({ error: "external_network" });
  }
}
async function waitExternal(job) {
  const phases = { waiting: "external.phase.waiting", requesting: "external.phase.requesting", downloading: "external.phase.downloading", parsing: "external.phase.parsing", reading: "external.phase.reading" };
  while (true) {
    const state = await api().external_progress(job);
    if (state.error) return state;
    if (state.done) return state.answer;
    const progress = document.getElementById("external-progress");
    if (state.total > 0) { progress.max = state.total; progress.value = state.received; }
    else progress.removeAttribute("value");
    document.getElementById("external-status").textContent = state.cancelling
      ? t("external.cancelling")
      : t(phases[state.phase] || "external.phase.waiting", { received: numberText(state.received), total: numberText(state.total), requests: numberText(state.requests) });
    await new Promise(resolve => setTimeout(resolve, 250));
  }
}
const PROVIDER_LABEL = {
  gutenberg: "external.gutenberg",
  ndl: "external.ndl",
  cbeta: "external.cbeta",
  "nikh-partial": "external.nikh",
  rise: "external.rise",
};
let currentExternalProvider = "";
function resetExternalInputs() {
  clearExternalPreview();
  document.getElementById("external-address").value = "";
  document.getElementById("external-revision").value = "";
  const offlineFile = document.getElementById("external-file-name");
  if (offlineFile) offlineFile.textContent = "";
  const status = document.getElementById("external-status");
  if (status) {
    status.textContent = "";
    status.classList.remove("is-error");
  }
  riseState.booksReqId++;
  riseState.sectionsReqId++;
  riseState.loadingBooks = false;
  riseState.loadingSections = false;
  riseState.books = [];
  riseState.sections = [];
  riseState.selectedBook = "";
  riseState.selectedSection = "";
  riseState.range = 1;
  riseState.totalUnits = null;
  riseState.totalRanges = 1;
  const bookSelect = document.getElementById("external-rise-book");
  if (bookSelect) bookSelect.replaceChildren();
  const secSelect = document.getElementById("external-rise-section");
  if (secSelect) secSelect.replaceChildren();
  const bookPage = document.getElementById("external-rise-book-page");
  if (bookPage) bookPage.textContent = "";
  const rangeLabel = document.getElementById("external-rise-range-label");
  if (rangeLabel) rangeLabel.textContent = "";
}
function openExternal(provider = "") {
  const select = document.getElementById("external-provider");
  const field = document.getElementById("external-provider-field");
  const target = provider || select.value;
  if (!currentExternalProvider) {
    currentExternalProvider = target;
  } else if (target !== currentExternalProvider) {
    resetExternalInputs();
    currentExternalProvider = target;
  }
  select.value = target;
  if (provider) {
    field.classList.add("is-hidden");
    externalDialog.querySelector(".dialog-title").textContent = t(PROVIDER_LABEL[provider]);
  } else {
    field.classList.remove("is-hidden");
    externalDialog.querySelector(".dialog-title").textContent = t("external.title");
  }
  document.getElementById("external-status").textContent = "";
  externalDialog.showModal();
  updateExternalProvider();
  updateExternal();
  // Land on the control actually used first, not on the header Close button.
  const opened = provider || select.value;
  const first = opened === "rise"
    ? document.getElementById("external-rise-book")
    : (["cbeta", "nikh-partial"].includes(opened)
      ? document.getElementById("external-file")
      : document.getElementById("external-address"));
  if (first) first.focus();
}
document.querySelectorAll("[data-external-provider]").forEach(button => {
  button.addEventListener("click", () => openExternal(button.dataset.externalProvider));
});
document.getElementById("external-find-link").addEventListener("click", event => {
  event.preventDefault();
  const link = event.currentTarget.getAttribute("href");
  if (link) openSourceLink(link);
});
const externalRevisionLink = document.getElementById("external-revision-link");
if (externalRevisionLink) {
  externalRevisionLink.addEventListener("click", event => {
    event.preventDefault();
    const link = event.currentTarget.getAttribute("href");
    if (link) openSourceLink(link);
  });
}
document.getElementById("ctp-find-link").addEventListener("click", event => {
  event.preventDefault();
  const link = event.currentTarget.getAttribute("href");
  if (link) openSourceLink(link);
});
document.querySelectorAll("dialog").forEach(dialog => {
dialog.addEventListener("keydown", event => {
  if (event.key !== "Tab") return;
  const focusable = [...dialog.querySelectorAll('button:not(:disabled), select:not(:disabled), input:not(:disabled), summary, textarea, a[href]')]
    .filter(el => el.getClientRects().length && !el.disabled && el.tabIndex >= 0);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey) {
    if (document.activeElement === first || !dialog.contains(document.activeElement)) {
      event.preventDefault();
      last.focus();
      last.scrollIntoView({ block: "nearest" });
    }
  } else {
    if (document.activeElement === last || !dialog.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
      first.scrollIntoView({ block: "nearest" });
    }
  }
});
dialog.addEventListener("focusin", event => {
  if (event.target && typeof event.target.scrollIntoView === "function") {
    event.target.scrollIntoView({ block: "nearest" });
  }
});
});
externalDialog.addEventListener("cancel", event => { if (external.busy) event.preventDefault(); });
externalDialog.querySelector("form").addEventListener("submit", event => { if (external.busy) event.preventDefault(); });
externalDialog.addEventListener("close", () => {
  riseState.booksReqId++;
  riseState.sectionsReqId++;
  riseState.loadingBooks = false;
  riseState.loadingSections = false;
  if (riseState.selectedBook && !riseState.sections.length) {
    riseState.selectedBook = "";
  }
  hideRiseError();
  clearExternalPreview();
});
document.getElementById("external-cancel").addEventListener("click", async () => {
  if (!external.job) return;
  external.cancelling = true; updateExternal();
  document.getElementById("external-status").textContent = t("external.cancelling");
  try { await api().cancel_external(external.job); }
  catch { external.cancelling = false; externalError({ error: "external_network" }); updateExternal(); }
});
document.getElementById("external-fetch").addEventListener("click", async () => {
  const status = document.getElementById("external-status");
  clearExternalPreview(); external.busy = true; external.cancelling = false;
  status.classList.remove("is-error"); status.textContent = t("external.working"); updateExternal();
  const provider = document.getElementById("external-provider").value;
  try {
    const started = await api().start_external_preview(provider, document.getElementById("external-address").value, document.getElementById("external-revision").value);
    if (started.error) { externalError(started); return; }
    external.job = started.job; updateExternal();
    const answer = await waitExternal(started.job);
    if (answer.error) {
      if (answer.error === "external_cancelled") status.textContent = t("external.cancelled");
      else externalError(answer);
      return;
    }
    external.token = answer.token; external.units = answer.units; external.report = Boolean(answer.details && answer.details.policy);
    showExternalExclusions(answer.details);
    if (provider === "rise" && answer.details && answer.details.coverage) {
      const cov = answer.details.coverage;
      riseState.totalUnits = cov.total_units ?? answer.units.length;
      riseState.totalRanges = Math.max(1, Math.ceil(riseState.totalUnits / (cov.per_page || 50)));
      syncRiseAddress();
      const covEl = document.getElementById("external-coverage");
      if (covEl) {
        covEl.textContent = t("external.rise.coverage", {
          returned: cov.returned_units ?? answer.units.length,
          total_units: cov.total_units ?? answer.units.length,
          accepted: cov.accepted_slices ?? answer.units.length,
          excluded: answer.details.excluded ? answer.details.excluded.length : 0,
        });
      }
    }
    document.getElementById("external-book").textContent = answer.title;
    document.getElementById("external-edition").value = answer.edition;
    document.getElementById("external-unit").replaceChildren(...answer.units.map((unit, idx) => {
      const option = document.createElement("option"); option.value = unit.id;
      const chars = typeof unit.characters === "number" ? unit.characters : (unit.characters?.length || 0);
      option.textContent = provider === "rise"
        ? t("external.rise.unit_label", { n: idx + 1, chars })
        : t("external.unit.label", unit);
      return option;
    }));
    const pages = answer.units.filter(unit => Number.isInteger(unit.page)).map(unit => unit.page);
    document.getElementById("external-range").classList.toggle("is-hidden", !pages.length);
    if (pages.length) { document.getElementById("external-from").value = pages[0]; document.getElementById("external-to").value = pages[pages.length - 1]; }
    document.getElementById("external-preview").classList.remove("is-hidden");
    await showExternalUnit(); status.textContent = t(answer.units.length ? "external.ready" : "external.empty");
  } catch {
    if (external.job) api().cancel_external(external.job)?.catch?.(() => {});
    externalError({ error: "external_network" });
  } finally {
    external.busy = false; external.job = ""; external.cancelling = false; updateExternal();
    if (externalDialog.open && !externalDialog.contains(document.activeElement)) {
      // A finished preview adds its body above: start reading there instead of
      // leaving focus stranded on the footer actions.
      const started = external.token && !document.getElementById("external-preview").classList.contains("is-hidden");
      (started ? document.getElementById("external-book") : document.getElementById("external-fetch")).focus();
    }
  }
});
function onExternalProvider() {
  const newProvider = document.getElementById("external-provider").value;
  if (!currentExternalProvider) {
    currentExternalProvider = newProvider;
  } else if (newProvider !== currentExternalProvider) {
    resetExternalInputs();
    currentExternalProvider = newProvider;
  }
  updateExternalProvider();
  updateExternal();
}
["input", "change"].forEach(evt => document.getElementById("external-provider").addEventListener(evt, onExternalProvider));
["external-address", "external-revision"].forEach(id => document.getElementById(id).addEventListener("input", () => {
  clearExternalPreview();
  updateExternal();
}));
document.getElementById("external-rise-book").addEventListener("change", event => {
  riseState.selectedBook = event.target.value;
  loadRiseSections(riseState.selectedBook);
});
document.getElementById("external-rise-book-prev").addEventListener("click", () => {
  if (riseState.booksPage > 1 && !riseState.loadingBooks) {
    loadRiseBooks(riseState.booksPage - 1);
  }
});
document.getElementById("external-rise-book-next").addEventListener("click", () => {
  if (riseState.booksPage < riseState.booksTotalPages && !riseState.loadingBooks) {
    loadRiseBooks(riseState.booksPage + 1);
  }
});
document.getElementById("external-rise-section").addEventListener("change", event => {
  riseState.selectedSection = event.target.value;
  riseState.range = 1;
  riseState.totalUnits = null;
  riseState.totalRanges = 1;
  clearExternalPreview();
  syncRiseAddress();
});
document.getElementById("external-rise-range-prev").addEventListener("click", () => {
  if (riseState.range > 1) {
    riseState.range--;
    clearExternalPreview();
    syncRiseAddress();
  }
});
document.getElementById("external-rise-range-next").addEventListener("click", () => {
  if (riseState.totalUnits !== null && riseState.range < riseState.totalRanges) {
    riseState.range++;
    clearExternalPreview();
    syncRiseAddress();
  }
});
document.getElementById("external-rise-retry").addEventListener("click", () => {
  if (typeof riseRetryAction === "function") riseRetryAction();
});
document.getElementById("external-report").addEventListener("click", async () => {
  if (!await ensureTargetFolder("external-status")) return;
  external.busy = true; updateExternal();
  try {
    const answer = await api().save_external_report(external.token);
    if (answer.error) { externalError(answer); return; }
    const status = document.getElementById("external-status");
    status.classList.remove("is-error"); status.textContent = t("external.report.saved", answer);
  } catch { externalError({ error: "release_write_failed" }); }
  finally { external.busy = false; updateExternal(); }
});
document.getElementById("external-file").addEventListener("click", async () => {
  try {
    const answer = await api().pick_external_xml(t("external.filetype"));
    if (answer.path) { clearExternalPreview(); document.getElementById("external-address").value = answer.path; updateExternal(); }
  } catch { externalError({ error: "external_offline_unreadable" }); }
});
document.getElementById("external-unit").addEventListener("change", () => { updateExternal(); showExternalUnit().catch(() => externalError({ error: "external_network" })); });
document.getElementById("external-include").addEventListener("change", event => {
  const id = document.getElementById("external-unit").value;
  if (event.target.checked) external.selected.add(id); else external.selected.delete(id);
  document.getElementById("external-reviewed").checked = false; updateExternal();
});
document.getElementById("external-all").addEventListener("click", () => {
  if (external.selected.size === external.units.length) external.selected.clear();
  else external.units.forEach(unit => external.selected.add(unit.id));
  document.getElementById("external-reviewed").checked = false; updateExternal();
});
document.getElementById("external-range-select").addEventListener("click", () => {
  const from = Number(document.getElementById("external-from").value), to = Number(document.getElementById("external-to").value);
  const units = external.units.filter(unit => unit.page >= from && unit.page <= to);
  if (!Number.isSafeInteger(from) || !Number.isSafeInteger(to) || from < 1 || to < from || !units.length) { externalError({ error: "external_range" }); return; }
  external.selected = new Set(units.map(unit => unit.id));
  document.getElementById("external-reviewed").checked = false;
  document.getElementById("external-status").classList.remove("is-error");
  document.getElementById("external-status").textContent = t("external.ready");
  updateExternal();
});
["external-reviewed", "external-edition"].forEach(id => document.getElementById(id).addEventListener("input", updateExternal));
document.getElementById("external-source").addEventListener("click", event => { event.preventDefault(); const link = event.currentTarget.getAttribute("href"); if (link) openSourceLink(link); });
document.getElementById("external-import").addEventListener("click", async () => {
  if (!await ensureTargetFolder("external-status")) return;
  external.busy = true; updateExternal();
  const status = document.getElementById("external-status");
  status.classList.remove("is-error"); status.textContent = t("external.saving");
  try {
    const answer = await api().import_external(external.token, [...external.selected], document.getElementById("external-edition").value, document.getElementById("external-reviewed").checked);
    if (answer.error) { externalError(answer); return; }
    external.token = ""; status.textContent = t("external.done", answer.imported);
    if (answer.library && !answer.library.error) { await acceptLibrary(answer.library); clearResults(); }
    else if (answer.library) { await refreshFailedImport(answer.library.error); externalError(answer.library); }
  } catch { externalError({ error: "external_network" }); }
  finally { external.busy = false; updateExternal(); }
});
const externalHistory = { books: [] };
function showExternalHistory() {
  const details = document.getElementById("external-history-details"), parts = document.getElementById("external-history-parts");
  details.replaceChildren(); parts.replaceChildren();
  const book = externalHistory.books[document.getElementById("external-history-book").selectedIndex];
  if (!book) return;
  const status = document.getElementById("external-history-status");
  if (book.error) { status.textContent = errorText(book.error); return; }
  status.textContent = "";
  for (const [key, value] of [["external.edition", book.edition], ["external.history.folder", book.folder], ["external.history.count", book.units]]) {
    const term = document.createElement("dt"), definition = document.createElement("dd");
    term.textContent = t(key); definition.textContent = String(value); details.append(term, definition);
  }
  book.receipts.forEach((receipt, index) => {
    const item = document.createElement("li"), heading = document.createElement("p"), selected = document.createElement("p");
    heading.textContent = t("external.history.part", { n: index + 1, units: receipt.selected.length });
    selected.textContent = receipt.selected.join(", "); item.append(heading, selected);
    if (receipt.details) {
      const exclusions = document.createElement("p");
      exclusions.textContent = t("external.excluded", { n: (receipt.details.excluded || []).length }); item.append(exclusions);
    }
    receipt.downloads.forEach(download => {
      const link = document.createElement("a"), hash = document.createElement("code"), line = document.createElement("p");
      link.textContent = download.url; link.href = download.url;
      link.addEventListener("click", event => { event.preventDefault(); openSourceLink(download.url); });
      hash.textContent = t("external.history.checksum", { sha256: download.sha256 });
      line.append(link, document.createElement("br"), hash); item.append(line);
    });
    parts.append(item);
  });
}
document.getElementById("external-history-book").addEventListener("change", showExternalHistory);
let historyCheck = 0;
async function refreshHistoryAvailability() {
  const button = document.getElementById("external-history-open");
  const note = document.getElementById("external-history-note");
  if (!button || !note) return;
  const check = ++historyCheck;
  try {
    const answer = await api().external_history();
    if (check !== historyCheck) return;
    if (answer.error) {
      // A failed check is not proof that history is absent; leave the entry
      // available so the dialog can report the error.
      button.disabled = false;
      note.classList.add("is-hidden");
      return;
    }
    const available = Array.isArray(answer.books) && answer.books.length > 0;
    button.disabled = !available;
    button.setAttribute("aria-disabled", String(!available));
    note.classList.toggle("is-hidden", available);
  } catch {
    if (check !== historyCheck) return;
    button.disabled = false;
    note.classList.add("is-hidden");
  }
}
document.getElementById("external-history-open").addEventListener("click", async () => {
  const status = document.getElementById("external-history-status");
  document.getElementById("external-history-dialog").showModal();
  document.getElementById("external-history-details").replaceChildren(); document.getElementById("external-history-parts").replaceChildren();
  document.getElementById("external-history-book").replaceChildren(); status.textContent = t("external.history.loading");
  try {
    const answer = await api().external_history();
    if (answer.error) { status.textContent = errorText(answer.error); return; }
    externalHistory.books = answer.books;
    document.getElementById("external-history-book").replaceChildren(...answer.books.map(book => new Option(book.title || book.book, book.book)));
    if (answer.books.length) showExternalHistory(); else status.textContent = t("external.history.empty");
  } catch { status.textContent = t("error.external_history_conflict"); }
});

// Institute original-text releases (cpe.local.portal, cpe.local.releases): the dialog
// asks for the institute, the release and the folder, in that order. The download and
// conversion run on the bridge's own thread, so the window stays usable while the
// bottom left line shows the work is running.
const INSTITUTES = ["nikh", "itkc", "aks"];
const releaseDialog = document.getElementById("release-dialog");
const release = { datasets: [], institute: "", pk: "" };

function choice(name, value, title, detail, extra) {
  const label = document.createElement("label");
  label.className = "choice";
  const input = document.createElement("input");
  input.type = "radio";
  input.name = name;
  input.value = value;
  const text = document.createElement("span");
  text.className = "choice-text";
  const strong = document.createElement("strong");
  strong.textContent = title;
  const small = document.createElement("span");
  small.className = "choice-detail";
  small.textContent = detail;
  text.append(strong, small);
  if (extra) text.append(extra);
  label.append(input, text);
  return label;
}
function renderInstitutes() {
  const box = document.getElementById("release-institutes");
  box.replaceChildren(...INSTITUTES.map(code => {
    const label = choice("release-institute", code, t(`release.inst.${code}`), t(`release.inst.${code}.desc`));
    const input = label.querySelector("input");
    input.checked = release.institute === code;
    input.addEventListener("change", () => { release.institute = code; release.pk = ""; renderDatasets(); updateRelease(); });
    return label;
  }));
}
function renderDatasets() {
  const box = document.getElementById("release-datasets");
  document.getElementById("release-dataset-hint").classList.toggle("is-hidden", Boolean(release.institute));
  box.replaceChildren(...release.datasets.filter(d => d.institute === release.institute).map(d => {
    const meta = document.createElement("span");
    meta.className = "choice-meta";
    const rights = document.createElement("span");
    rights.textContent = t(`release.rights.${d.rights}`);
    const page = document.createElement("button");
    page.type = "button";
    page.className = "link-button";
    page.textContent = t("release.page");
    page.addEventListener("click", event => { event.preventDefault(); openSourceLink(d.page); });
    meta.append(rights, page);
    const label = choice("release-dataset", d.pk, t(`release.ds.${d.pk}`), t(`release.ds.${d.pk}.desc`), meta);
    const input = label.querySelector("input");
    input.checked = release.pk === d.pk;
    input.addEventListener("change", () => { release.pk = d.pk; updateRelease(); });
    return label;
  }));
}
function updateRelease() {
  document.getElementById("release-start").disabled = !release.pk;
}
document.getElementById("release-open").addEventListener("click", async () => {
  const status = document.getElementById("release-status");
  if (status) { status.textContent = ""; status.classList.remove("is-error"); }
  try {
    if (!release.datasets.length) release.datasets = await api().datasets();
    renderInstitutes();
    renderDatasets();
    updateRelease();
    releaseDialog.showModal();
    const firstInstitute = document.querySelector("#release-institutes input");
    if (firstInstitute) firstInstitute.focus();
  } catch {
    renderInstitutes();
    renderDatasets();
    updateRelease();
    releaseDialog.showModal();
    if (status) {
      status.classList.add("is-error");
      status.textContent = t("error.portal_failed");
    }
  }
});
async function runRelease(taskText, call, name) {
  showView("search");
  const task = beginLocalBusy();
  releaseDialog.setAttribute("aria-busy", "true");
  batchDialog.setAttribute("aria-busy", "true");
  try { await runReleaseTask(taskText, call, name); }
  finally {
    releaseDialog.setAttribute("aria-busy", "false");
    batchDialog.setAttribute("aria-busy", "false");
    endLocalBusy(task);
  }
}
async function runReleaseTask(taskText, call, name) {
  const status = document.getElementById("status");
  const task = document.getElementById("task");
  const opener = document.getElementById("release-open");
  status.classList.remove("is-error");
  status.textContent = "";
  document.getElementById("batch-details").classList.add("is-hidden");
  opener.disabled = true;
  document.getElementById("task-text").textContent = taskText;
  task.title = t("release.working");
  task.classList.remove("is-hidden");
  let answer;
  try {
    answer = await call();
  } catch {
    answer = { error: "release_write_failed" };
  } finally {
    task.classList.add("is-hidden");
  }
  if (answer.cancelled) return;
  if (answer.error) {
    status.classList.add("is-error");
    status.textContent = errorText(answer.error, answer);
    syncResultsBar();
    return;
  }
  // Saved texts need a fresh folder snapshot before they can be searched.
  const unopened = answer.library && answer.library.error ? answer.library : null;
  if (unopened) await refreshFailedImport(unopened.error);
  if (answer.library && !unopened) {
    await acceptLibrary(answer.library);
    clearResults();
  }
  if (answer.batch) {
    showBatch(answer.batch, answer.source, unopened);
    return;
  }
  if (unopened) {
    status.classList.add("is-error");
    status.textContent = t("release.unopened", unopened) + " " + errorText(unopened.error, unopened);
    syncResultsBar();
    return;
  }
  // An institute release may leave out articles it cannot write faithfully
  // (cpe.nikh.importer); the count and the report's place are said with the result.
  const key = answer.imported.skipped_articles ? "release.done.skipped" : "release.done";
  status.textContent = t(key, { ...answer.imported, book: name || answer.imported.book });
  syncResultsBar();
}
const batchDialog = document.getElementById("batch-dialog");
// One entry per release file of a folder import. Paths and titles come from the
// researcher's files, so they are set as text only.
function batchItem(item) {
  const li = document.createElement("li");
  li.classList.toggle("is-failed", item.status === "failed");
  const head = document.createElement("p");
  head.className = "batch-line";
  const file = document.createElement("span");
  file.className = "batch-file";
  file.textContent = item.path;
  const state = document.createElement("span");
  state.className = "batch-state";
  state.textContent = t(`batch.status.${item.status}`);
  head.append(file, state);
  li.append(head);
  const lines = [];
  if (item.status === "failed") lines.push(errorText(item.code, item));
  else lines.push(t("batch.item.saved", item));
  if (item.skipped_articles) lines.push(t("batch.item.excluded", item));
  for (const text of lines) {
    const line = document.createElement("p");
    line.className = "batch-line";
    line.textContent = text;
    li.append(line);
  }
  return li;
}
// A folder import is shown as clean only when every file was imported or was
// already there and no article was left out; otherwise the status line says what
// failed or was left out, and the details list every file.
function showBatch(batch, source, unopened) {
  const status = document.getElementById("status");
  const details = document.getElementById("batch-details");
  const summary = batch.total
    ? t(batch.failed || batch.excluded_articles ? "batch.summary.issues" : "batch.summary", batch)
    : t("batch.none", { source });
  document.getElementById("batch-summary").textContent = summary;
  const load = document.getElementById("batch-load");
  load.textContent = unopened ? t("release.unopened", unopened) : "";
  load.classList.toggle("is-hidden", !unopened);
  document.getElementById("batch-items").replaceChildren(...batch.items.map(batchItem));
  status.classList.toggle("is-error", Boolean(unopened || batch.failed));
  status.textContent = unopened ? t("release.unopened", unopened) : summary;
  details.classList.toggle("is-hidden", !batch.total);
  syncResultsBar();
}
document.getElementById("batch-details").addEventListener("click", () => batchDialog.showModal());
document.getElementById("release-start").addEventListener("click", async () => {
  const { pk } = release;
  if (!pk) return;
  if (!await ensureTargetFolder("release-status")) return;
  const name = t(`release.ds.${pk}`);
  releaseDialog.close();
  runRelease(t("release.background.download", { name }), () => api().import_dataset(pk, library.folder), name);
});
document.getElementById("release-directory").addEventListener("click", async () => {
  if (!await ensureTargetFolder("release-status")) return;
  releaseDialog.close();
  runRelease(t("release.background"), () => api().import_releases(library.folder), "");
});
document.getElementById("release-file").addEventListener("click", async () => {
  if (!await ensureTargetFolder("release-status")) return;
  releaseDialog.close();
  runRelease(t("release.background"), () => api().import_release(t("release.filetype"), library.folder), "");
});

const form = document.getElementById("search");
function mode() { return form.querySelector("input[name=mode]:checked").value; }
// Every searchable source, in menu order. "local" is the text folder; the others are
// online and each has a catalog name source.<code> and an explanation source.<code>.note.
// Adding a source here adds its selection and contextual guide controls.
const SOURCES = ["local", "itkc", "ndl", "cbeta", "cyberseodang", "docusky", "historydb", "sillok", "ith", "nahf", "nrich", "vietdien", "sat", "aksarchive", "ships", "thdl", "csid", "cand", "kyudb"];
const ONLINE = SOURCES.filter(code => code !== "local");
// Browsing groups follow source/service context, not operator addresses or script.
// Keep SOURCES order stable for saved choices and result-panel ordering.
const SOURCE_REGIONS = Object.freeze([
  { id: "korea", label: "sources.region.korea", sources: ["itkc", "cyberseodang", "historydb", "sillok", "nahf", "nrich", "aksarchive", "kyudb"] },
  { id: "japan", label: "sources.region.japan", sources: ["ndl", "sat", "ships", "cand"] },
  { id: "china", label: "sources.region.china", sources: ["csid"] },
  { id: "taiwan", label: "sources.region.taiwan", sources: ["cbeta", "docusky", "ith", "thdl"] },
  { id: "vietnam", label: "sources.region.vietnam", sources: ["vietdien"] },
]);

// Several sources can be searched at once, each in its own panel (at most four: 田).
const MAX_SOURCES = 4;
const sourceChips = document.getElementById("source-chips");
const sourceOptions = new Map();
const sourceHelpButtons = new Map();
const sourceRegionGrids = new Map();
for (const region of SOURCE_REGIONS) {
  const section = document.createElement("section");
  section.className = "source-region";
  section.dataset.sourceRegion = region.id;
  const heading = document.createElement("h4");
  heading.id = `source-region-${region.id}`;
  heading.dataset.i18n = region.label;
  section.setAttribute("aria-labelledby", heading.id);
  const grid = document.createElement("div");
  grid.className = "source-region-grid";
  section.append(heading, grid);
  sourceChips.appendChild(section);
  for (const code of region.sources) sourceRegionGrids.set(code, grid);
}
function sourceInfoIcon() {
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  icon.setAttribute("focusable", "false");
  const mark = document.createElementNS("http://www.w3.org/2000/svg", "path");
  mark.setAttribute("d", "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 6v.01M12 12v5");
  icon.append(mark);
  return icon;
}
const sourceBoxes = ONLINE.map(code => {
  const option = document.createElement("div");
  option.className = "source-option";
  option.dataset.source = code;
  const label = document.createElement("label");
  label.className = "chip";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.value = code;
  box.checked = false;
  const name = document.createElement("span");
  name.dataset.i18n = `source.${code}`;
  label.append(box, " ", name);
  const help = document.createElement("button");
  help.type = "button";
  help.className = "source-help";
  help.dataset.i18nTitle = "source.help";
  help.append(sourceInfoIcon());
  help.setAttribute("aria-controls", "source-guide");
  help.setAttribute("aria-expanded", "false");
  help.addEventListener("click", () => showSourceGuide(code));
  option.append(label, help);
  sourceOptions.set(code, option);
  sourceHelpButtons.set(code, help);
  sourceRegionGrids.get(code).appendChild(option);
  return box;
});
const localFilesHelp = document.getElementById("local-files-help");
localFilesHelp.append(sourceInfoIcon());
localFilesHelp.addEventListener("click", () => showSourceGuide("local"));
sourceHelpButtons.set("local", localFilesHelp);
sourceOptions.set("local", document.getElementById("local-files-help-row"));

// The integrated NIKH search is one source with an era and a database-item choice.
// The provider counts the whole integrated search but serves rows only from one era
// item, so the window asks for both before sending the one-page query.
const historydbBox = sourceBoxes[ONLINE.indexOf("historydb")];
const historydbSites = ["diachronic", "ancient", "goryeo", "joseon", "modern", "contemp"];
const historydbControls = document.createElement("span");
historydbControls.className = "historydb-controls is-hidden";
const historydbSiteSelect = document.createElement("select");
historydbSiteSelect.className = "historydb-site";
historydbSiteSelect.dataset.i18nAria = "source.historydb.site_label";
for (const site of historydbSites) {
  const option = document.createElement("option");
  option.value = site;
  option.dataset.i18n = `source.historydb.site.${site}`;
  historydbSiteSelect.appendChild(option);
}
historydbSiteSelect.value = "joseon";
const historydbItemSelect = document.createElement("select");
historydbItemSelect.className = "historydb-item";
historydbItemSelect.dataset.i18nAria = "source.historydb.item";
const historydbPrompt = document.createElement("option");
historydbPrompt.value = "";
historydbPrompt.dataset.i18n = "source.historydb.item_prompt";
historydbItemSelect.appendChild(historydbPrompt);
historydbControls.append(historydbSiteSelect, historydbItemSelect);
sourceOptions.get("historydb").appendChild(historydbControls);
let historydbSite = "joseon";
let historydbItem = "";
const historydbCatalog = new Map();
// The era whose catalogue request is in flight, or "" when none is. An era chosen
// meanwhile is queued instead of dropped, so the dropdown never keeps the old
// era's "loading" row.
let historydbLoadingSite = "";
let historydbReload = false;

function fillHistorydbItems(items) {
  const keep = historydbItem;
  historydbItemSelect.replaceChildren();
  const prompt = document.createElement("option");
  prompt.value = "";
  prompt.dataset.i18n = items.length ? "source.historydb.item_prompt" : "source.historydb.no_item";
  prompt.textContent = t(prompt.dataset.i18n);
  historydbItemSelect.appendChild(prompt);
  for (const entry of items) {
    const option = document.createElement("option");
    option.value = entry.id;
    option.textContent = entry.label || entry.id;
    historydbItemSelect.appendChild(option);
  }
  historydbItem = items.some(entry => entry.id === keep) ? keep : "";
  historydbItemSelect.value = historydbItem;
}

async function loadHistorydbItems() {
  const site = historydbSite;
  if (historydbCatalog.has(site)) {
    fillHistorydbItems(historydbCatalog.get(site));
    return;
  }
  if (historydbLoadingSite) {
    if (historydbLoadingSite !== site) historydbReload = true;
    return;
  }
  historydbLoadingSite = site;
  historydbItem = "";
  historydbItemSelect.replaceChildren();
  const loading = document.createElement("option");
  loading.value = "";
  loading.textContent = t("source.historydb.catalog_loading");
  historydbItemSelect.appendChild(loading);
  try {
    const answer = await api().historydb_catalog(site);
    // Another era was chosen while this answered: the queued reload words the
    // dropdown, so this answer is only kept for when that era is chosen again.
    if (answer.error || !Array.isArray(answer.items)) {
      if (historydbSite !== site) return;
      const failed = document.createElement("option");
      failed.value = "";
      failed.disabled = true;
      failed.textContent = t("source.historydb.catalog_failed");
      historydbItemSelect.replaceChildren(failed);
      return;
    }
    historydbCatalog.set(site, answer.items);
    if (historydbSite === site) fillHistorydbItems(answer.items);
  } catch {
    if (historydbSite !== site) return;
    const failed = document.createElement("option");
    failed.value = "";
    failed.disabled = true;
    failed.textContent = t("source.historydb.catalog_failed");
    historydbItemSelect.replaceChildren(failed);
  } finally {
    historydbLoadingSite = "";
    if (historydbReload) {
      historydbReload = false;
      if (historydbBox.checked) loadHistorydbItems();
    }
  }
}

historydbSiteSelect.addEventListener("change", async () => {
  historydbSite = historydbSiteSelect.value;
  historydbBox.checked = true;
  updateSource();
  loadHistorydbItems();
  await saveSources();
});
historydbItemSelect.addEventListener("change", async () => {
  historydbItem = historydbItemSelect.value;
  historydbBox.checked = true;
  updateSource();
  await saveSources();
});
historydbBox.addEventListener("change", () => {
  historydbControls.classList.toggle("is-hidden", !historydbBox.checked);
  if (historydbBox.checked) loadHistorydbItems();
});

// NAHF returns rows only for one source-library collection at a time. The
// collection catalog is local metadata; the selected id is sent with the query.
const nahfBox = sourceBoxes[ONLINE.indexOf("nahf")];
const nahfControls = document.createElement("span");
nahfControls.className = "nahf-controls is-hidden";
const nahfItemSelect = document.createElement("select");
nahfItemSelect.className = "nahf-item";
nahfItemSelect.dataset.i18nAria = "source.nahf.item";
const nahfPrompt = document.createElement("option");
nahfPrompt.value = "";
nahfPrompt.dataset.i18n = "source.nahf.item_prompt";
nahfItemSelect.appendChild(nahfPrompt);
let nahfItem = "";
let nahfCatalogLoaded = false;
let nahfLoading = false;
// The sub-option choices are saved beside the source list. They are applied
// before the catalogues load, so the historydb item and NAHF collection can be
// restored once their options arrive.
function applySourceOptions(options) {
  if (!options || typeof options !== "object") return;
  if (
    typeof options.historydb_site === "string"
    && historydbSites.includes(options.historydb_site)
  ) {
    historydbSite = options.historydb_site;
    historydbSiteSelect.value = historydbSite;
  }
  if (typeof options.historydb_item === "string") historydbItem = options.historydb_item;
  if (typeof options.nahf_item === "string") nahfItem = options.nahf_item;
}
nahfControls.append(nahfItemSelect);
sourceOptions.get("nahf").appendChild(nahfControls);

function fillNahfItems(items) {
  const keep = nahfItem;
  nahfItemSelect.replaceChildren();
  const prompt = document.createElement("option");
  prompt.value = "";
  prompt.dataset.i18n = "source.nahf.item_prompt";
  prompt.textContent = t(prompt.dataset.i18n);
  nahfItemSelect.appendChild(prompt);
  for (const entry of items) {
    const option = document.createElement("option");
    option.value = entry.id;
    option.textContent = entry.label || entry.id;
    nahfItemSelect.appendChild(option);
  }
  nahfItem = items.some(entry => entry.id === keep) ? keep : "";
  nahfItemSelect.value = nahfItem;
}

async function loadNahfItems() {
  if (nahfCatalogLoaded || nahfLoading) return;
  nahfLoading = true;
  const loading = document.createElement("option");
  loading.value = "";
  loading.textContent = t("source.nahf.catalog_loading");
  nahfItemSelect.replaceChildren(loading);
  try {
    const answer = await api().nahf_catalog();
    if (answer.error || !Array.isArray(answer.items)) {
      const failed = document.createElement("option");
      failed.value = "";
      failed.disabled = true;
      failed.textContent = t("source.nahf.catalog_failed");
      nahfItemSelect.replaceChildren(failed);
      return;
    }
    nahfCatalogLoaded = true;
    fillNahfItems(answer.items);
  } catch {
    const failed = document.createElement("option");
    failed.value = "";
    failed.disabled = true;
    failed.textContent = t("source.nahf.catalog_failed");
    nahfItemSelect.replaceChildren(failed);
  } finally {
    nahfLoading = false;
  }
}

nahfItemSelect.addEventListener("change", async () => {
  nahfItem = nahfItemSelect.value;
  nahfBox.checked = true;
  updateSource();
  await saveSources();
});
nahfBox.addEventListener("change", () => {
  nahfControls.classList.toggle("is-hidden", !nahfBox.checked);
  if (nahfBox.checked) loadNahfItems();
});

// DocuSky searches one public database at a time. The dropdown offers the reviewed
// database plus every database the provider's public catalogue lists; those are
// labelled unreviewed, because their body layers have not been inspected.
const docuskyBox = sourceBoxes[ONLINE.indexOf("docusky")];
const docuskyProfiles = document.createElement("span");
docuskyProfiles.className = "docusky-profiles is-hidden";
const docuskyLabel = document.createElement("span");
docuskyLabel.dataset.i18n = "source.docusky.database";
const docuskySelect = document.createElement("select");
docuskySelect.className = "docusky-database";
const reviewedOption = document.createElement("option");
reviewedOption.value = "docusky";
reviewedOption.dataset.i18n = "source.docusky";
docuskySelect.appendChild(reviewedOption);
const docuskyUnreviewed = new Map();
function docuskyOption(source, database, reviewed = false) {
  const option = document.createElement("option");
  option.value = source;
  option.textContent = reviewed
    ? database
    : `${database} (${t("source.docusky.unreviewed")})`;
  docuskySelect.appendChild(option);
  if (!reviewed) docuskyUnreviewed.set(option, database);
  return option;
}
function labelDocuskyOptions() {
  for (const [option, database] of docuskyUnreviewed) {
    option.textContent = `${database} (${t("source.docusky.unreviewed")})`;
  }
  const failed = docuskySelect.querySelector("option[data-failed]");
  if (failed) failed.textContent = t("source.docusky.catalog_failed");
}
docuskyProfiles.append(docuskyLabel, docuskySelect);
sourceOptions.get("docusky").appendChild(docuskyProfiles);

docuskySelect.addEventListener("change", async () => {
  docuskyBox.value = docuskySelect.value;
  docuskyBox.checked = true;
  updateSource();
  await saveSources();
});
let docuskyCatalogLoaded = false;
function docuskyCatalogFailed() {
  docuskyCatalogLoaded = false;
  if ([...docuskySelect.options].some(option => option.dataset.failed)) return;
  const option = document.createElement("option");
  option.disabled = true;
  option.dataset.failed = "1";
  option.textContent = t("source.docusky.catalog_failed");
  docuskySelect.appendChild(option);
}
async function loadDocuskyDatabases() {
  try {
    const answer = await api().docusky_catalog();
    if (answer.error || !Array.isArray(answer.databases)) {
      docuskyCatalogFailed();
      return;
    }
    const keep = docuskySelect.value;
    docuskySelect.querySelectorAll("option[data-failed]").forEach(option => option.remove());
    for (const entry of answer.databases) {
      if (!entry.source || !entry.database) continue;
      if (entry.database === answer.default) continue;
      const existing = [...docuskySelect.options].find(option => option.value === entry.source);
      if (existing) {
        if (entry.reviewed) {
          docuskyUnreviewed.delete(existing);
          existing.textContent = entry.database;
        }
        continue;
      }
      docuskyOption(entry.source, entry.database, Boolean(entry.reviewed));
    }
    if ([...docuskySelect.options].some(option => option.value === keep)) {
      docuskySelect.value = keep;
    }
  } catch { docuskyCatalogFailed(); }
}
docuskyBox.addEventListener("change", () => {
  docuskyProfiles.classList.toggle("is-hidden", !docuskyBox.checked);
  if (docuskyBox.checked && !docuskyCatalogLoaded) {
    docuskyCatalogLoaded = true;
    loadDocuskyDatabases();
  }
});
const nrichBox = sourceBoxes[ONLINE.indexOf("nrich")];
const nrichHint = document.getElementById("nrich-hint");
if (nrichBox && nrichHint) {
  nrichBox.addEventListener("change", () => {
    nrichHint.classList.toggle("is-hidden", !nrichBox.checked);
  });
}

// Each local slot keeps its own library, selection and result panel identity.
const localRows = LOCAL_SOURCES.map(code => {
  const row = document.createElement("div"); row.className = "local-source is-hidden";
  const label = document.createElement("label"); label.className = "chip";
  const box = document.createElement("input"); box.type = "checkbox"; box.value = code;
  const title = document.createElement("span"); label.append(box, title);
  const actions = document.createElement("span"); actions.className = "local-actions";
  const load = document.createElement("button"); load.type = "button"; load.className = "open local-load";
  load.addEventListener("click", () => loadLocalSlot(code));
  const choose = document.createElement("button"); choose.type = "button"; choose.className = "open";
  choose.addEventListener("click", () => {
    autoLoadTried.delete(code);
    chooseLocalFolder(code);
  });
  const remove = document.createElement("button"); remove.type = "button"; remove.className = "link-button local-remove";
  remove.dataset.i18n = "sources.remove";
  remove.addEventListener("click", () => askRemoveLocal(code));
  actions.append(load, choose, remove);
  const detail = document.createElement("p"); detail.className = "local-detail";
  const pathDetails = document.createElement("details"); pathDetails.className = "local-path-details";
  const pathSummary = document.createElement("summary"); pathSummary.dataset.i18n = "local.path_details";
  const path = document.createElement("p"); path.className = "local-path";
  pathDetails.append(pathSummary, path);
  const skipped = document.createElement("details"); skipped.className = "local-skipped is-hidden";
  const skippedSummary = document.createElement("summary"); skippedSummary.dataset.i18n = "library.skipped.summary";
  const skippedText = document.createElement("p"); skippedText.className = "library-skipped";
  skipped.append(skippedSummary, skippedText);
  row.append(label, actions, detail, pathDetails, skipped);
  document.getElementById("local-sources").append(row);
  return { code, row, box, title, load, choose, remove, path, detail, skipped, skippedText, pathDetails };
});

document.getElementById("add-local-folder").addEventListener("click", () => {
  const code = LOCAL_SOURCES.find(code => !localInfo(code)?.configured);
  if (code) chooseLocalFolder(code);
});

const sourceGuide = document.getElementById("source-guide");
const sourceGuideLayout = document.getElementById("source-guide-layout");
const sourceWorkspace = document.getElementById("source-workspace");
const sourceGuideTitle = document.getElementById("source-guide-title");
const sourceGuideBody = document.getElementById("source-guide-body");
const sourceOfficial = document.getElementById("source-guide-official");
const sourceOfficialLink = document.getElementById("source-guide-link");
const NRICH_OFFICIAL = "https://portal.nrich.go.kr/kor/mokganList.do?menuIdx=891";
const SOURCE_OFFICIAL = {
  nrich: NRICH_OFFICIAL,
  cand: "https://tool.hankeidou.jp/cand/kanshi_search1.php",
  kyudb: "https://kyudb.snu.ac.kr/main.do?mid=GMS",
};
let sourceOfficialUrl = "";
sourceOfficialLink.addEventListener("click", event => {
  event.preventDefault();
  if (sourceOfficialUrl) openSourceLink(sourceOfficialUrl);
});
function updateSourceOfficial(code) {
  sourceOfficialUrl = SOURCE_OFFICIAL[code] || "";
  sourceOfficial.classList.toggle("is-hidden", !sourceOfficialUrl);
  if (sourceOfficialUrl) sourceOfficialLink.href = sourceOfficialUrl;
  else sourceOfficialLink.removeAttribute("href");
}

let sourceGuideCode = "";
const GUIDE_HEADINGS = Object.freeze({
  scope: "guide.scope",
  provided: "guide.provided",
  limits: "guide.limits",
  verification: "guide.verification",
  usage: "guide.usage",
});
function sourceGuideSections(code) {
  const prefix = code === "local" ? "library.guide" : `source.${code}.guide`;
  return Object.entries(GUIDE_HEADINGS).flatMap(([category, label]) => {
    const key = `${prefix}.${category}`;
    const text = t(key);
    return text === key ? [] : [{ category, label: t(label), text }];
  });
}
function renderSourceGuideBody(code) {
  const sections = sourceGuideSections(code);
  if (!sections.length) {
    // An older catalog can still explain its source without exposing raw keys.
    sourceGuideBody.textContent = code === "local" ? t("library.help") : t(`source.${code}.note`);
    return;
  }
  sourceGuideBody.replaceChildren(...sections.map(({ category, label, text }) => {
    const section = document.createElement("section");
    section.className = "source-guide-section";
    section.dataset.category = category;
    const heading = document.createElement("h5");
    heading.textContent = label;
    const paragraphs = text.split("\n\n").map(value => {
      const paragraph = document.createElement("p");
      paragraph.textContent = value;
      return paragraph;
    });
    section.append(heading, ...paragraphs);
    return section;
  }));
}
function refreshSourceGuide() {
  for (const [code, button] of sourceHelpButtons) {
    button.setAttribute("aria-label", code === "local" ? t("library.help.summary")
      : t("source.help_for", { source: t(`source.${code}`) }));
    button.setAttribute("aria-expanded", String(code === sourceGuideCode));
  }
  if (!sourceGuideCode) return;
  const code = sourceGuideCode;
  sourceGuideTitle.textContent = code === "local" ? t("library.help.summary") : t(`source.${code}`);
  renderSourceGuideBody(code);
  updateSourceOfficial(code);
}

function placeSourceGuide() {
  if (!sourceGuideCode) return;
  const button = sourceHelpButtons.get(sourceGuideCode);
  let trigger = button.getBoundingClientRect();
  if (trigger.bottom < 0 || trigger.top > window.innerHeight) {
    button.scrollIntoView({ block: "nearest" });
    trigger = button.getBoundingClientRect();
  }
  const owner = sourceOptions.get(sourceGuideCode).getBoundingClientRect();
  const viewport = window.visualViewport;
  const bounds = { x: viewport?.offsetLeft || 0, y: viewport?.offsetTop || 0,
    width: viewport?.width || window.innerWidth, height: viewport?.height || window.innerHeight };
  const gutter = 12, gap = 8;
  const room = Math.max(1, bounds.width - gutter * 2);
  const font = parseFloat(getComputedStyle(sourceGuide).fontSize) || 14;
  const width = Math.min(room, Math.max(owner.width, Math.min(bounds.width * .42, font * 44)));
  const left = Math.max(bounds.x + gutter, Math.min(owner.left, bounds.x + bounds.width - gutter - width));
  sourceGuide.style.width = `${width}px`;
  sourceGuide.style.maxHeight = `${Math.max(1, bounds.height - gutter * 2)}px`;
  const desired = sourceGuide.getBoundingClientRect().height;
  const below = bounds.y + bounds.height - gutter - trigger.bottom - gap;
  const above = trigger.top - bounds.y - gutter - gap;
  let top, height = desired;
  if (desired <= below) top = trigger.bottom + gap;
  else if (desired <= above) top = trigger.top - gap - desired;
  else if (Math.max(above, below) >= Math.min(desired, font * 12)) {
    height = Math.max(above, below);
    top = below >= above ? trigger.bottom + gap : bounds.y + gutter;
  } else {
    // A short window needs usable reading space, not a tiny anchored sliver.
    // Keep this overlay out of flow even when it must overlap its trigger.
    top = Math.max(bounds.y + gutter, Math.min(trigger.bottom + gap, bounds.y + bounds.height - gutter - desired));
  }
  height = Math.min(height, Math.max(1, bounds.height - gutter * 2));
  top = Math.max(bounds.y + gutter, Math.min(top, bounds.y + bounds.height - gutter - height));
  sourceGuide.style.maxHeight = `${height}px`;
  sourceGuide.style.left = `${left}px`;
  sourceGuide.style.top = `${top}px`;
}

function closeSourceGuide({ restoreFocus = true } = {}) {
  if (!sourceGuideCode) return false;
  const trigger = sourceHelpButtons.get(sourceGuideCode);
  sourceGuideCode = "";
  if (typeof sourceGuide.hidePopover === "function" && sourceGuide.matches(":popover-open")) sourceGuide.hidePopover();
  sourceGuide.classList.add("is-hidden");
  refreshSourceGuide();
  if (restoreFocus) trigger.focus({ preventScroll: true });
  return true;
}

function showSourceGuide(code) {
  if (!sourceHelpButtons.has(code)) return;
  if (sourceGuideCode === code) { closeSourceGuide(); return; }
  sourceGuideCode = code;
  sourceGuide.classList.remove("is-hidden");
  refreshSourceGuide();
  sourceGuideTitle.parentElement.scrollTop = 0;
  if (typeof sourceGuide.showPopover === "function") {
    if (!sourceGuide.matches(":popover-open")) sourceGuide.showPopover({ source: sourceHelpButtons.get(code) });
  } else sourceGuide.removeAttribute("popover");
  placeSourceGuide();
  sourceGuideTitle.focus({ preventScroll: true });
}
document.getElementById("source-guide-close").addEventListener("click", () => closeSourceGuide());
document.addEventListener("pointerdown", event => {
  if (!sourceGuideCode || sourceGuide.contains(event.target) || event.target.closest(".source-help")) return;
  closeSourceGuide({ restoreFocus: false });
});
window.addEventListener("resize", placeSourceGuide);
window.visualViewport?.addEventListener("resize", placeSourceGuide);
window.visualViewport?.addEventListener("scroll", placeSourceGuide);
document.addEventListener("scroll", event => {
  if (!sourceGuideCode || sourceGuide.contains(event.target)) return;
  const rect = sourceHelpButtons.get(sourceGuideCode).getBoundingClientRect();
  if (rect.bottom < 0 || rect.top > window.innerHeight) closeSourceGuide({ restoreFocus: false });
  else placeSourceGuide();
}, true);
function allSourceBoxes() { return [...localRows.map(row => row.box), ...sourceBoxes]; }
function sources() { return allSourceBoxes().filter(box => box.checked).map(box => box.value); }
function selectedLocalReady(code) {
  const info = localInfo(code);
  return Boolean(info?.configured && !info.load_error);
}
function hasUsableSources() {
  return sources().some(code => LOCAL_SOURCES.includes(code) ? selectedLocalReady(code) : true);
}
function updateWelcome() {
  const help = document.getElementById("welcome-help");
  help.classList.toggle("is-hidden", hasUsableSources());
}
function updateSource() {
  const chosen = sources();
  allSourceBoxes().forEach(box => {
    const missing = LOCAL_SOURCES.includes(box.value) && !selectedLocalReady(box.value);
    if (missing && box.checked) box.checked = false;
    box.disabled = savingSources || missing || (!box.checked && chosen.length >= MAX_SOURCES);
  });
  showLocalControls();
  docuskyProfiles.classList.toggle("is-hidden", !docuskyBox.checked);
  if (historydbControls) {
    historydbControls.classList.toggle("is-hidden", !historydbBox.checked);
    if (
      historydbBox.checked
      && !historydbCatalog.has(historydbSite)
      && historydbLoadingSite !== historydbSite
    ) {
      loadHistorydbItems();
    }
  }
  if (nahfControls) {
    nahfControls.classList.toggle("is-hidden", !nahfBox.checked);
    if (nahfBox.checked && !nahfCatalogLoaded && !nahfLoading) loadNahfItems();
  }
  if (nrichBox && nrichHint) nrichHint.classList.toggle("is-hidden", !nrichBox.checked);
  updateWelcome();
  updateSelectionNotice();
  updateSearchScope();
  syncSourceChoiceReason();
  syncSearchButton();
}
const homeButton = document.getElementById("home");
homeButton.addEventListener("click", () => showView("search"));
document.querySelectorAll(".top-nav [data-view]").forEach(button => {
  button.addEventListener("click", () => showView(button.dataset.view));
});
document.getElementById("import-originals").addEventListener("click", () => showImportProviders(true));
document.getElementById("import-providers-back")?.addEventListener("click", () => showImportProviders(false));
document.getElementById("sources-import-route")?.addEventListener("click", () => showView("import"));
let topFrame = null;
const reserveTop = () => {
  if (topFrame !== null) return;
  topFrame = requestAnimationFrame(() => {
    topFrame = null;
    const topEl = document.querySelector(".top");
    if (!topEl) return;
    const height = getComputedStyle(topEl).position === "static" ? 0 : Math.ceil(topEl.getBoundingClientRect().height);
    const space = `${height + 12}px`;
    const style = document.documentElement.style;
    if (style.getPropertyValue("--top-h") !== `${height}px`)
      style.setProperty("--top-h", `${height}px`);
    if (style.getPropertyValue("--top-space") !== space) style.setProperty("--top-space", space);
  });
};
const topHeader = document.querySelector(".top");
if (topHeader) {
  if (typeof ResizeObserver === "function") new ResizeObserver(reserveTop).observe(topHeader);
  else window.addEventListener("resize", reserveTop);
  reserveTop();
}
allSourceBoxes().forEach(box => box.addEventListener("change", async () => {
  updateSource();
  updateSelectionNotice();
  await saveSources();
}));
let searchVersion = 0;
// One search at a time: the bridge calls cannot be cancelled, and a second run
// would spend another source's request budget while the first still answers.
let searchBusy = false;
// The run token owns one submitted search: its entries, pending replies, stop
// acknowledgement and finalizer. A finished run stays here so a retry can
// reuse the same lifecycle and original request.
let activeRun = null;
let terminalStatusKey = "";
function syncResultsBar() {
  const bar = document.getElementById("results-bar");
  if (!bar) return;
  const hasPanels = Boolean(document.querySelector("#panels .panel"));
  const hasStatus = Boolean(document.getElementById("status").textContent);
  bar.classList.toggle("is-hidden", !hasPanels && !hasStatus);
}
function syncSearchButton() {
  const button = document.querySelector("#search .go");
  if (!button) return;
  const run = typeof activeRun === "undefined" ? null : activeRun;
  const active = Boolean(run && !run.finished);
  // An empty source selection is routed to the source view by the submit
  // handler; disabling the button here would make that feedback unreachable.
  button.disabled = searchBusy;
  document.querySelectorAll("#panels .source-retry").forEach(retry => { retry.disabled = searchBusy; });
  const stop = typeof document.getElementById === "function" ? document.getElementById("search-stop") : null;
  if (stop) {
    stop.classList.toggle("is-hidden", !active);
    stop.disabled = !active || Boolean(run.stopRequested);
  }
  const clear = typeof document.getElementById === "function" ? document.getElementById("results-clear") : null;
  if (clear) clear.disabled = Boolean(active && !run.stopRequested);
  form.setAttribute("aria-busy", String(searchBusy));
  syncResultsBar();
}
function updateSelectionNotice() {
  const notice = document.getElementById("selection-notice");
  if (!notice) return;
  const submitted = lastAnswers.map(entry => entry.source);
  const selected = sources();
  const changed = submitted.length > 0 && (submitted.length !== selected.length
    || submitted.some((source, index) => source !== selected[index])
    || lastAnswers.some(({ source, request }) => source === "historydb"
      ? request?.site !== historydbSite || request?.item !== historydbItem
      : source === "nahf" && request?.item !== nahfItem));
  notice.classList.toggle("is-hidden", !changed);
  notice.textContent = changed ? t("results.selection_changed") : "";
}

// The next search's target, shown beside the fields. `#selection-notice` says
// instead that the panels on screen were submitted for a different selection;
// keeping them separate stops a changed selection from relabelling old results.
function updateSearchScope() {
  const label = document.getElementById("search-scope-label");
  const list = document.getElementById("search-scope-list");
  const count = document.getElementById("search-scope-count");
  const change = document.getElementById("search-scope-change");
  if (!label || !list || !count || !change) return;
  const chosen = sources();
  // One entry per selected ID/slot, in source order: two slots may open the same
  // folder and still be distinct search targets, and the count counts slots.
  label.textContent = t("search.scope.label");
  change.textContent = t("search.scope.change");
  count.textContent = chosen.length
    ? t("search.scope.count", { n: numberText(chosen.length), max: numberText(MAX_SOURCES) })
    : t("search.scope.empty");
  list.replaceChildren(...chosen.map(code => {
    const item = document.createElement("li");
    item.dataset.source = code;
    item.textContent = sourceName(code);
    return item;
  }));
  list.classList.toggle("is-hidden", chosen.length === 0);
}

// Only a submit with no usable source shows this reason; ordinary navigation to
// the Sources view must not report a search error the researcher did not make.
function showSourceChoiceReason() {
  const note = document.getElementById("sources-selection-notice");
  if (!note) return;
  note.classList.remove("is-hidden");
  note.textContent = t("sources.selection_required");
}
function clearSourceChoiceReason() {
  const note = document.getElementById("sources-selection-notice");
  if (!note) return;
  note.textContent = "";
  note.classList.add("is-hidden");
}
function syncSourceChoiceReason() {
  const note = document.getElementById("sources-selection-notice");
  if (!note || note.classList.contains("is-hidden")) return;
  // A visible reason follows the catalog and disappears as soon as the
  // selection can run, so it never lingers over a usable target.
  if (hasUsableSources()) { clearSourceChoiceReason(); return; }
  const text = t("sources.selection_required");
  // Repeating the same sentence on every source edit would re-announce it.
  if (note.textContent !== text) note.textContent = text;
}

function clearResults() {
  const run = typeof activeRun === "undefined" ? null : activeRun;
  if (run && !run.finished) {
    // Clearing while calls are in flight is a stop request, not an unlock: the
    // held requests must finish before another search can start.
    run.cleared = true;
    if (!run.stopRequested) void requestSearchStop({ fromClear: true });
    else setStatusKey(run.stopAck === "failed" ? "results.stop_unconfirmed" : "results.stopping");
    resultMotion.reset();
    lastAnswers = [];
    document.getElementById("panels").replaceChildren();
    updateWelcome();
    updateSelectionNotice();
    syncSearchButton();
    return;
  }
  searchVersion++;
  terminalStatusKey = "";
  activeRun = null;
  api().invalidate_searches?.(searchVersion)?.catch(() => {});
  resultMotion.reset();
  lastAnswers = [];
  document.getElementById("status").textContent = "";
  document.getElementById("panels").replaceChildren();
  updateWelcome();
  updateSelectionNotice();
  syncSearchButton();
}
document.getElementById("results-clear").addEventListener("click", clearResults);
document.getElementById("search-scope-change").addEventListener("click", () => showView("sources"));
const searchStopButton = document.getElementById("search-stop");
if (searchStopButton) searchStopButton.addEventListener("click", () => { void requestSearchStop(); });
function updateHint() {
  form.classList.toggle("is-compound", mode() === "pair");
  document.getElementById("search-help").textContent = t(mode() === "pair" ? "search.hint.compound" : "search.hint.single");
}

// Marks from the server (spec 11): 【strong】, 〖weak〗, and a backslash before a source
// character that would otherwise read as a mark. Built as DOM nodes, never as HTML.
function marked(text) {
  const out = document.createDocumentFragment();
  let buffer = "";
  let open = null;
  const flush = () => {
    if (!buffer) return;
    if (open) {
      const el = document.createElement(open === "【" ? "mark" : "span");
      el.className = open === "【" ? "term" : "weak";
      el.textContent = buffer;
      out.appendChild(el);
    } else {
      out.appendChild(document.createTextNode(buffer));
    }
    buffer = "";
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "\\" && i + 1 < text.length) { buffer += text[++i]; continue; }
    if (c === "【" || c === "〖") { flush(); open = c; continue; }
    if (c === "】" || c === "〗") { flush(); open = null; continue; }
    buffer += c;
  }
  flush();
  return out;
}

const CITATION_FIELDS = Object.freeze([
  ["item_id", "source.vietdien.citation_item"],
  ["title_han", "source.vietdien.citation_title"],
  ["page", "source.vietdien.citation_page"],
  ["number", "source.sat.citation_no"],
  ["pages", "source.sat.citation_pages"],
  ["hits", "source.sat.citation_hits"],
  ["uci", "source.aksarchive.citation_uci"],
  ["date", "source.aksarchive.citation_date"],
  ["report", "source.nrich.citation_report"],
  ["institution", "source.nrich.citation_institution"],
  ["rights", "source.nrich.citation_rights"],
  ["thdl_reference", "source.thdl.citation_reference"],
  ["thdl_file", "source.thdl.citation_file"],
  ["cand_collection", "source.cand.citation_collection"],
  ["kyudb_number", "source.kyudb.citation_number"],
  ["kyudb_parties", "source.kyudb.citation_parties"],
  ["kyudb_volume", "source.kyudb.citation_volume"],
  ["kyudb_date", "source.kyudb.citation_date"],
  ["kyudb_type", "source.kyudb.citation_type"],
  ["kyudb_classification", "source.kyudb.citation_classification"],
  ["csid_dynasty", "source.csid.citation_dynasty"],
  ["csid_date", "source.csid.citation_date"],
  ["csid_kind", "source.csid.citation_kind"],
  ["csid_number", "source.csid.citation_number"],
]);

function card(row, number) {
  const node = document.getElementById("card").content.firstElementChild.cloneNode(true);
  if (typeof row.id === "string" && (row.id.startsWith("csid:") || row.id.startsWith("cand:") || (row.id.startsWith("kyudb:") && !row.segment))) {
    node.querySelector(".text").hidden = true;
  }
  if (typeof row.id === "string" && (row.id.indexOf("nrich:") === 0 || row.id.indexOf("vietdien:") === 0 || row.id.indexOf("aksarchive:") === 0 || row.id.indexOf("kyudb:") === 0)) {
    // Mixed Hanja/Idu (NRICH), Hán/Nôm OCR (Việt Điện) or Korean/Han manuscripts
    // (AKS archive, Kyujanggak): no determined language. Keep the supplied text
    // without assigning a reading language from its characters.
    for (const part of [".text", ".before", ".seg", ".after"]) {
      const span = node.querySelector(part);
      if (span) span.setAttribute("lang", "und");
    }
  }
  // The position in its group, so a result can be found again by number.
  node.dataset.number = String(number);
  node.querySelector(".num").textContent = String(number);
  const src = node.querySelector(".src");
  src.textContent = row.title;
  if (row.link) {
    // Opens the source's own page in the system browser, not inside the window.
    src.dataset.link = row.link;
    src.addEventListener("click", event => { event.preventDefault(); openSourceLink(row.link); });
  } else {
    src.removeAttribute("href");
  }
  node.querySelector(".era").textContent = eraText(row.era);
  if (row.matched_field) {
    node.dataset.matchedField = row.matched_field;
  }
  const reasons = node.querySelector(".reasons");
  row.reasons.forEach(code => {
    const span = document.createElement("span");
    span.className = "reason";
    span.textContent = t(`reason.${code}`);
    reasons.appendChild(span);
  });
  node.querySelector(".before").appendChild(marked(row.before));
  node.querySelector(".seg").appendChild(marked(row.segment));
  node.querySelector(".after").appendChild(marked(row.after));
  const citation = node.querySelector(".citation");
  if (citation && row.citation) {
    const parts = [];
    for (const [field, label] of CITATION_FIELDS) {
      if (row.citation[field]) parts.push(t(label, { value: row.citation[field] }));
    }
    if (parts.length) {
      citation.textContent = parts.join(" · ");
      citation.classList.remove("is-hidden");
    }
  }
  const footer = node.querySelector("footer");
  if (row.kind === "occurrence") {
    footer.classList.add("is-hidden");
  } else {
    const a = node.querySelector(".a .v");
    if (row.a) a.textContent = row.a;
    else { a.textContent = t("slot.none"); a.classList.add("none"); }
    const b = node.querySelector(".b .v");
    if (row.b.length) b.textContent = row.b.join(" · ");
    else { b.textContent = t("slot.none"); b.classList.add("none"); }
  }
  // A and B are labels for the two text slots, not for the query elements.
  // The complete localized label is shown once above its value; the value below
  // keeps the source characters and lang unchanged.
  for (const [slotName, labelKey] of [["a", "slot.a.label"], ["b", "slot.b.label"]]) {
    const label = node.querySelector(`.slot.${slotName} .slot-label`);
    if (!label) continue;
    label.textContent = t(labelKey);
  }
  return node;
}

function pageStartFor(number) {
  return Math.max(0, Math.floor((number - 1) / PAGE) * PAGE);
}

// A new fill replaces the pending page; opening must never use an older locale,
// sort or answer snapshot. Weak keys let removed result panels be collected.
const resultPages = new WeakMap();
function renderGroupPage(section) {
  if (section.isConnected === false || section.classList.contains("is-closed")
    || section.classList.contains("is-hidden")) return;
  resultPages.get(section)?.render();
}
function setGroupExpanded(section, expanded) {
  section.classList.toggle("is-closed", !expanded);
  section.querySelector(".group-toggle").setAttribute("aria-expanded", String(expanded));
  if (expanded) renderGroupPage(section);
}

function fill(section, rows, labelKey, keepVisibleWhenEmpty = false, keepHidden = false) {
  const list = section.querySelector(".list");
  const navigation = section.querySelector(".result-pages");
  if (navigation) {
    navigation.hidden = true;
    navigation.querySelector(".page-previous").onclick = null;
    navigation.querySelector(".page-next").onclick = null;
    navigation.querySelector(".page-range").textContent = "";
  }
  let startValue = Number(section.dataset.start || 0);
  if (!Number.isFinite(startValue)) startValue = 0;
  let start = Math.max(0, Math.floor(startValue / PAGE) * PAGE);
  if (start >= rows.length) start = 0;
  resultMotion.forget(list);
  list.replaceChildren();
  section.querySelector(".label").textContent = t(labelKey);
  section.querySelector(".n").textContent = t("results.count", { n: rows.length });
  section.classList.toggle("is-hidden", keepHidden || (!keepVisibleWhenEmpty && rows.length === 0 && section.classList.contains("less")));
  if (!rows.length) {
    resultPages.delete(section);
    section.querySelector(".jump").hidden = true;
    section.querySelector(".jump-to").onkeydown = null;
    const jumpButton = section.querySelector(".jump-go");
    if (jumpButton) jumpButton.onclick = null;
    section.dataset.start = "0";
    section.dataset.shown = String(PAGE);
    const empty = document.createElement("p");
    empty.className = "card none";
    empty.textContent = t("results.none");
    list.appendChild(empty);
    return;
  }
  const page = { dirty: true, render: () => { if (page.dirty) showPage(start); } };
  resultPages.set(section, page);
  let shown = start;
  const syncPages = () => {
    if (!navigation) return;
    navigation.hidden = rows.length <= PAGE;
    navigation.querySelector(".page-previous").disabled = start === 0;
    navigation.querySelector(".page-next").disabled = shown >= rows.length;
    navigation.querySelector(".page-range").textContent = t("results.page_range", {
      from: numberText(start + 1), to: numberText(shown), total: numberText(rows.length),
    });
  };
  const showPage = (targetStart, move = false) => {
    start = targetStart;
    shown = Math.min(start + PAGE, rows.length);
    resultMotion.forget(list);
    list.replaceChildren();
    page.dirty = true;
    if (section.isConnected !== false && !section.classList.contains("is-closed")
      && !section.classList.contains("is-hidden")) {
      list.replaceChildren(...rows.slice(start, shown).map((row, i) => card(row, start + i + 1)));
      page.dirty = false;
    }
    section.dataset.start = String(start);
    section.dataset.shown = String(shown);
    syncPages();
    if (move && !page.dirty) section.scrollIntoView?.({ block: "start" });
  };
  if (navigation) {
    navigation.querySelector(".page-previous").onclick = () => {
      if (start > 0) showPage(Math.max(0, start - PAGE), true);
    };
    navigation.querySelector(".page-next").onclick = () => {
      if (shown < rows.length) showPage(shown, true);
    };
  }
  showPage(start);
  setupJump(section, rows.length, number => {
    const targetStart = pageStartFor(number);
    // A number inside the rendered window only needs scrolling. A distant
    // number replaces the window with its page instead of building every card
    // from the first result to the target.
    if (page.dirty || targetStart < start || targetStart >= shown) {
      showPage(targetStart);
    }
    const target = list.querySelector(`.card[data-number="${number}"]`);
    if (!target) return;
    // Measured on screen (offsetTop depends on which ancestor is positioned), and
    // again once newly rendered cards and late fonts have settled their heights.
    const align = () => {
      if (resultPages.get(section) !== page || page.dirty || section.isConnected === false
        || target.isConnected === false || section.classList.contains("is-closed")
        || section.classList.contains("is-hidden")) return;
      target.scrollIntoView?.({ block: "start" });
    };
    align();
    requestAnimationFrame(() => requestAnimationFrame(align));
    document.fonts.ready.then(align);
  });
}

// The number box at the top right of a group: pick a suggested number or type one,
// and the list scrolls so that result is at the top.
function setupJump(section, total, go) {
  const box = section.querySelector(".jump-to");
  const options = section.querySelector(".jump datalist");
  if (!section.id) section.id = `group-${Math.random().toString(36).slice(2)}`;
  options.id = `${section.id}-jumps`;
  box.setAttribute("list", options.id);
  box.max = String(total);
  box.value = "";
  const step = total > 500 ? 100 : 50;
  options.replaceChildren(...Array.from({ length: Math.ceil(total / step) }, (_, i) => {
    const option = document.createElement("option");
    option.value = String(i * step + 1);
    return option;
  }));
  section.querySelector(".jump").hidden = total <= 1;
  const jump = () => {
    const entered = Number(box.value);
    if (!box.value.trim() || !Number.isFinite(entered)) return;
    const number = Math.min(Math.max(Math.trunc(entered), 1), total);
    box.value = String(number);
    // A closed group hides its list, so scrolling it would move an invisible node
    // and the requested result would never appear. Open the group before `go`.
    section.classList.remove("is-closed");
    section.querySelector(".group-toggle").setAttribute("aria-expanded", "true");
    go(number);
  };
  const button = section.querySelector(".jump-go");
  if (button) {
    button.textContent = t("jump.go");
    button.onclick = jump;
  }
  // No change or blur handler: picking a suggestion, editing, or leaving the box
  // must not move the list. Only the Go button or Enter jumps explicitly.
  box.onkeydown = event => {
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229 || event.repeat || event.defaultPrevented) return;
    event.preventDefault();
    jump();
  };
  // Typing in the box must not fold the group.
  box.onclick = event => event.stopPropagation();
}

// A year as text; years before the common era are negative in the data.
function yearText(value) {
  if (value === null || value === undefined) return "";
  return value < 0 ? t("era.bce", { n: -value }) : String(value);
}
// When a result dates from, as its source records it (cpe.gui.service rows: era).
// An online row may carry its provider's era name instead of a date
// (cpe.gui.online historydb rows: era is the subsite code).
function eraText(era) {
  if (!era) return "";
  if (typeof era === "string") return t(`source.historydb.site.${era}`);
  const year = value => yearText(value) || "?";
  if (era.kind === "author_life") return t("era.author_life", { from: year(era.from), to: year(era.to) });
  if (era.kind === "published") {
    const sourceText = String(era.text ?? "").trim();
    // Some sources repeat the numeric year as their date label. Suppress only
    // that exact duplicate; a source era name or qualified date stays intact.
    const text = sourceText === String(era.year) ? "" : sourceText;
    return t("era.published", { year: era.year, text }).trim();
  }
  if (era.kind === "dynasty") return t("era.dynasty", { dynasty: era.dynasty, from: year(era.from), to: year(era.to) }).trim();
  if (era.kind === "source_date") return String(era.label || "");
  if (era.kind === "manifest") {
    // The researcher's own label and years, shown as written: no catalog words.
    const years = yearText(era.from) && (yearText(era.to) && era.to !== era.from
      ? `${yearText(era.from)}–${yearText(era.to)}` : yearText(era.from));
    return [era.label, years].filter(Boolean).join(" ");
  }
  return "";
}
function sorted(rows) {
  const order = document.getElementById("sort").value;
  if (order !== "era" && order !== "era-desc") return rows;
  // Unknown dates stay last in both directions; equal dates keep source order.
  const direction = order === "era-desc" ? -1 : 1;
  const key = row => (row.era && Number.isFinite(row.era.year) ? direction * row.era.year : Infinity);
  return rows.map((row, index) => [row, index]).sort((a, b) => key(a[0]) - key(b[0]) || a[1] - b[1]).map(pair => pair[0]);
}
let sortSaveVersion = 0;
let sortSaveQueue = Promise.resolve();
let sortSaveError = false;
function updateSortStatus() {
  const status = document.getElementById("sort-status");
  if (!status) return;
  status.textContent = sortSaveError ? t("sort.not_saved") : "";
  status.classList.toggle("is-hidden", !sortSaveError);
}
async function restoreSort() {
  const version = sortSaveVersion;
  try {
    const order = await api().saved_sort();
    if (version !== sortSaveVersion || !["source", "era", "era-desc"].includes(order)) return;
    document.getElementById("sort").value = order;
    renderAll();
  } catch {
    // An unavailable optional preference must not prevent startup or searching.
  }
}
function saveSort() {
  const order = document.getElementById("sort").value;
  const version = ++sortSaveVersion;
  sortSaveError = false;
  updateSortStatus();
  // Serialize writes so an earlier selection cannot win after a later one.
  // Queued selections that are already obsolete need not be written.
  sortSaveQueue = sortSaveQueue.then(async () => {
    if (version !== sortSaveVersion) return;
    let saved = false;
    try { saved = await api().save_sort(order); } catch {}
    if (version !== sortSaveVersion) return;
    sortSaveError = !saved;
    updateSortStatus();
  });
  return sortSaveQueue;
}
document.getElementById("sort").addEventListener("change", () => {
  renderAll();
  return saveSort();
});

// What an online source returned, so the researcher knows how much was checked.
function sourceSummary(info) {
  if (!info) return "";
  if (info.name === "itkc") {
    return info.collections.map(c =>
      t("source.itkc.collection", { name: t(`source.itkc.${c.code}`), records: c.records, total: c.total })).join(" · ");
  }
  if (info.name === "ndl") return t("source.ndl.summary", info);
  if (info.name === "cbeta") return t("source.cbeta.summary", info);
  if (info.name === "cyberseodang") return t("source.cyberseodang.summary", info);
  if (info.name === "historydb") {
    const summary = t("source.historydb.summary", info);
    const parts = [t(`source.historydb.site.${info.site}`)];
    if (info.item_label) parts.push(info.item_label);
    return `${summary} · ${parts.join(" · ")}`;
  }
  if (info.name === "sillok") return t("source.sillok.summary", info);
  if (info.name === "ith") return t("source.ith.summary", info);
  if (info.name === "nahf") {
    const summary = t("source.nahf.summary", info);
    return info.item_name ? `${summary} · ${info.item_name}` : summary;
  }
  if (info.name === "vietdien") return t("source.vietdien.summary", info);
  if (info.name === "sat") return t("source.sat.summary", info);
  if (info.name === "aksarchive") return t("source.aksarchive.summary", info);
  if (info.name === "ships") return t("source.ships.summary", info);
  if (info.name === "thdl") return t("source.thdl.summary", info);
  if (info.name === "csid") return t("source.csid.summary", info);
  if (info.name === "cand") return t("source.cand.summary", info);
  if (info.name === "kyudb") return t("source.kyudb.summary", info);
  if (info.name === "nrich") {
    const coverage = info.coverage || {};
    const key = coverage.capped
      ? "source.nrich.summary_capped"
      : coverage.stopped_short
        ? "source.nrich.summary_short"
        : "source.nrich.summary";
    return t(key, {
      items: info.items,
      rows: coverage.rows ?? info.rows,
      cap: coverage.row_cap ?? 600,
      phrase: info.phrase,
    });
  }
  if (info.name === "docusky") {
    const summary = t("source.docusky.summary", info);
    return info.database && info.reviewed === false
      ? `${summary} · ${info.database} (${t("source.docusky.unreviewed")})`
      : summary;
  }
  return "";
}

let lastAnswers = []; // [{source, answer, request}] in panel order; null answers are pending
function makePanels(names) {
  resultMotion.reset();
  document.getElementById("welcome-help").classList.add("is-hidden");
  const box = document.getElementById("panels");
  box.className = `panels n${names.length}`;
  box.replaceChildren(...names.map(name => {
    const panel = document.getElementById("panel").content.firstElementChild.cloneNode(true);
    panel.dataset.source = name;
    translate(panel);
    panel.querySelectorAll(".group").forEach((group, index) => {
      const toggle = group.querySelector(".group-toggle");
      const list = group.querySelector(".list");
      list.id = `results-${name}-${index}`;
      toggle.setAttribute("aria-controls", list.id);
      toggle.addEventListener("click", () => {
        setGroupExpanded(group, group.classList.contains("is-closed"));
      });
    });
    resultMotion.observe(panel);
    return panel;
  }));
}
const REFERENCE_SOURCES = Object.freeze({
  nrich: { label: "source.nrich.candidates", note: "source.nrich.candidate_note" },
  csid: { label: "source.csid.candidates", note: "source.csid.candidate_note" },
  cand: { label: "source.cand.candidates", note: "source.cand.candidate_note" },
  kyudb: { label: "source.kyudb.candidates", note: "source.kyudb.candidate_note" },
});

function renderPanel(panel, name, answer, request) {
  panel.querySelector(".panel-title").textContent = sourceName(name);
  // Reading the live form here would relabel old results after an input edit.
  const query = panel.querySelector(".panel-query");
  if (query) {
    let modeKey = "", value = "";
    if (request?.mode === "words" && typeof request.query === "string") {
      modeKey = "search.single";
      value = request.query;
    } else if (request?.mode === "pair" && typeof request.first === "string" && typeof request.second === "string") {
      modeKey = "search.compound";
      value = t("results.query_pair", request);
    }
    query.classList.toggle("is-hidden", !modeKey);
    query.querySelector(".query-mode").textContent = modeKey ? t(modeKey) : "";
    query.querySelector(".query-value").textContent = value;
  }
  const status = panel.querySelector(".panel-status");
  const likely = panel.querySelector(".likely");
  const less = panel.querySelector(".less");
  const retry = panel.querySelector(".source-retry");
  if (retry) {
    retry.classList.toggle("is-hidden", Boolean(panel.dataset.stopped) || !retryableSourceError(answer));
    retry.disabled = searchBusy;
    retry.onclick = () => retrySource(lastAnswers.findIndex(entry => entry.source === name));
  }
  const external = panel.querySelector(".external-source");
  if (external) {
    external.classList.add("is-hidden");
    const link = external.querySelector("a");
    link.removeAttribute("href");
    link.onclick = null;
  }
  if (answer && answer.stopped) {
    status.textContent = t("results.source_stopped");
    status.classList.remove("is-error");
    panel.classList.remove("is-loading");
    likely.classList.add("is-hidden");
    less.classList.add("is-hidden");
    return;
  }
  status.classList.toggle("is-error", Boolean(answer && answer.error));
  panel.classList.toggle("is-loading", !answer);
  if (!answer) {
    status.textContent = t("results.searching");
    likely.classList.add("is-hidden");
    less.classList.add("is-hidden");
    return;
  }
  if (answer.error) {
    status.textContent = errorText(answer.error, answer);
    likely.classList.add("is-hidden");
    less.classList.add("is-hidden");
    return;
  }
  status.textContent = LOCAL_SOURCES.includes(name)
    ? t("library.folder", { n: numberText(textCount(localInfo(name))), folder: localInfo(name)?.folder || "" })
    : sourceSummary(answer.source);
  if (name === "ships" && answer.source?.external_only === true && external) {
    likely.classList.add("is-hidden");
    less.classList.add("is-hidden");
    const link = external.querySelector("a");
    link.textContent = t("source.external.open");
    link.href = answer.external_search_url;
    link.onclick = event => {
      event.preventDefault();
      openSourceLink(answer.external_search_url);
    };
    external.classList.remove("is-hidden");
    return;
  }
  likely.classList.remove("is-hidden");
  let likelyLabel = answer.mode === "pair" ? "results.likely" : "results.all";
  let lessLabel = "results.less";
  if (name === "thdl") likelyLabel = "source.thdl.candidates";
  if (name === "cyberseodang") {
    likelyLabel = "source.cyberseodang.checked";
    lessLabel = "source.cyberseodang.unchecked";
    if (!panel.dataset.received) less.classList.remove("is-closed");
  }
  const referenceOnly = Object.prototype.hasOwnProperty.call(REFERENCE_SOURCES, name);
  if (referenceOnly) {
    lessLabel = REFERENCE_SOURCES[name].label;
    if (!panel.dataset.received) less.classList.remove("is-closed");
  }
  const lessNote = less.querySelector(".group-note");
  if (lessNote) {
    lessNote.textContent = referenceOnly ? t(REFERENCE_SOURCES[name].note) : "";
    lessNote.classList.toggle("is-hidden", !referenceOnly);
  }
  panel.querySelectorAll(".group").forEach(group => {
    group.querySelector(".group-toggle").setAttribute("aria-expanded", String(!group.classList.contains("is-closed")));
  });
  panel.dataset.received = "true";
  fill(likely, sorted(answer.likely), likelyLabel, false, referenceOnly);
  fill(less, sorted(answer.less), lessLabel, referenceOnly);
  if (referenceOnly) likely.classList.add("is-hidden");
}
function renderAll() {
  const panels = [...document.querySelectorAll("#panels > .panel")];
  lastAnswers.forEach(({ source, answer, request }, index) => {
    if (panels[index]) renderPanel(panels[index], source, answer, request);
  });
}

form.addEventListener("keydown", event => {
  if (event.key !== "Enter" || event.target.tagName !== "INPUT") return;
  // IME composition confirms with Enter; do not submit the search mid-composition.
  if (event.isComposing || event.keyCode === 229) event.preventDefault();
});
function retryableSourceError(answer) {
  return ["network", "source_failed", "source_internal"].includes(answer?.error);
}

function setStatusKey(key, { preserveExternal = false } = {}) {
  const status = document.getElementById("status");
  // Imports share this status region. A late search finalizer must yield if
  // another operation has replaced the last text this run wrote.
  if (preserveExternal && activeRun && status && status.textContent !== activeRun.statusText) {
    terminalStatusKey = "";
    return;
  }
  terminalStatusKey = key;
  if (status) {
    status.textContent = key ? t(key) : "";
    if (activeRun) activeRun.statusText = status.textContent;
    status.classList?.remove?.("is-error");
  }
  if (typeof syncResultsBar === "function") syncResultsBar();
}

// Re-word active/terminal search state after a locale change without touching a
// validation message that has no run behind it.
function syncSearchStatus() {
  const status = document.getElementById("status");
  if (!status) return;
  const run = typeof activeRun === "undefined" ? null : activeRun;
  if (run && status.textContent !== run.statusText) {
    terminalStatusKey = "";
    return;
  }
  if (run && !run.finished) {
    if (run.stopRequested) {
      setStatusKey(run.stopAck === "failed" ? "results.stop_unconfirmed" : "results.stopping");
    }
    return;
  }
  if (terminalStatusKey) {
    setStatusKey(terminalStatusKey);
  }
}

function beginSearchRun(version, entries) {
  const run = {
    version,
    entries,
    pending: new Set(),
    stopRequested: false,
    stopAck: "none",
    finished: false,
    cleared: false,
    statusText: "",
    presentationFailed: false,
  };
  activeRun = run;
  searchBusy = true;
  terminalStatusKey = "";
  return run;
}
function trackPending(run) {
  const token = {};
  run.pending.add(token);
  searchBusy = true;
  return token;
}
function settlePending(run, token) {
  if (!run || !token) return;
  run.pending.delete(token);
  maybeFinishRun(run);
}
function runErrorCount(run) {
  return run.entries.filter(entry => entry.answer?.error && !entry.answer.stopped).length;
}
function finishRun(run) {
  if (!run || run.finished || activeRun !== run) return;
  run.finished = true;
  searchBusy = false;
  const superseded = run.version !== searchVersion && !run.stopRequested;
  if (superseded) terminalStatusKey = "";
  else if (run.stopRequested) {
    // A failed acknowledgement is only a draining-state warning; the terminal
    // state is the generic stopped message, or blank when the rows were cleared.
    terminalStatusKey = run.cleared ? "" : "results.stopped";
  } else {
    terminalStatusKey = run.presentationFailed ? "error.source_internal"
      : runErrorCount(run) ? "results.complete_errors" : "results.complete";
  }
  setStatusKey(terminalStatusKey, { preserveExternal: true });
  if (run.cleared) {
    lastAnswers = [];
    activeRun = null;
  }
  syncSearchButton();
}
function maybeFinishRun(run) {
  if (!run || run.finished || activeRun !== run) return;
  if (run.pending.size) return;
  if (run.stopRequested && run.stopAck === "pending") return;
  finishRun(run);
}

// Stop request: mark pending entries informational, invalidate the generation,
// and wait for both the calls and the bridge acknowledgement before unlocking.
async function requestSearchStop({ fromClear = false } = {}) {
  const run = typeof activeRun === "undefined" ? null : activeRun;
  if (!run || run.finished) return false;
  if (fromClear) run.cleared = true;
  if (run.stopRequested) return false;
  run.stopRequested = true;
  run.stopAck = "pending";
  for (const entry of run.entries) {
    entry.stopped = true;
    if (!entry.answer) entry.answer = { stopped: true };
  }
  document.querySelectorAll("#panels > .panel").forEach(panel => { panel.dataset.stopped = "true"; });
  try {
    if (typeof renderAll === "function") renderAll();
  } catch {
    // A panel rendering failure must not skip generation invalidation or the
    // acknowledgement wait; the run still has to drain and unlock safely.
  }
  setStatusKey("results.stopping");
  syncSearchButton();
  const generation = ++searchVersion;
  let ack = false;
  try {
    const apiObject = typeof api === "function" ? api() : null;
    ack = apiObject && typeof apiObject.invalidate_searches === "function"
      ? await apiObject.invalidate_searches(generation)
      : false;
  } catch {
    ack = false;
  }
  // The stop ack may arrive after another run took ownership; never let this
  // run overwrite that run's state.
  if (activeRun !== run || run.finished) return true;
  run.stopAck = ack === true ? "confirmed" : "failed";
  if (run.stopAck === "failed") setStatusKey("results.stop_unconfirmed", { preserveExternal: true });
  maybeFinishRun(run);
  return true;
}
// Public alias used by the stop control; both names share one lifecycle.
const stopSearch = requestSearchStop;

async function receiveSource(run, entry, index, token) {
  try {
    if (!run || run.finished || run.stopRequested || run.cleared
      || run.version !== searchVersion || activeRun !== run) return;
    const source = entry.source;
    let answer;
    try {
      answer = await api().search(entry.request);
    } catch {
      answer = { error: "network" };
    }
    if (!run || run.finished || run.stopRequested || run.cleared
      || run.version !== searchVersion || activeRun !== run) return;
    // A first search may have loaded this saved slot; refresh its count before
    // the panel words the source line.
    if (LOCAL_SOURCES.includes(source)) {
      try {
        const refreshed = await api().local_libraries();
        if (!run || run.finished || run.stopRequested || run.cleared
          || run.version !== searchVersion || activeRun !== run) return;
        localState = refreshed;
        updateSource();
      } catch {
        // Keep the counts already on screen; the search answer is still valid.
      }
    }
    if (!run || run.finished || run.stopRequested || run.cleared
      || run.version !== searchVersion || activeRun !== run) return;
    entry.answer = answer;
    const panel = document.querySelectorAll("#panels > .panel")[index];
    if (panel) renderPanel(panel, source, answer, entry.request);
  } catch (error) {
    run.presentationFailed = true;
    throw error;
  } finally {
    settlePending(run, token);
  }
}
async function retrySource(index) {
  const run = typeof activeRun === "undefined" ? null : activeRun;
  const entry = lastAnswers[index];
  if (!run || !run.finished || run.stopRequested || run.cleared) return;
  if (!entry?.request || !retryableSourceError(entry.answer)) return;
  // Retry reuses the run, its original immutable request and the same stop
  // lifecycle; it never creates a second search version.
  run.finished = false;
  run.stopAck = "none";
  run.presentationFailed = false;
  searchBusy = true;
  terminalStatusKey = "";
  setStatusKey("");
  syncSearchButton();
  try {
    entry.answer = null;
    const panel = document.querySelectorAll("#panels > .panel")[index];
    if (panel) renderPanel(panel, entry.source, null, entry.request);
    const token = trackPending(run);
    await receiveSource(run, entry, index, token);
  } catch (error) {
    run.presentationFailed = true;
    if (run.pending.size === 0) maybeFinishRun(run);
    throw error;
  }
}
form.addEventListener("submit", async event => {
  event.preventDefault();
  if (searchBusy) return;
  if (!hasUsableSources()) {
    // The reason belongs to this submit, so it is shown only here; the Sources
    // view itself stays free of search errors during normal navigation.
    showSourceChoiceReason();
    showView("sources");
    return;
  }
  const fields = mode() === "pair" ? ["first", "second"] : ["query"];
  fields.forEach(id => document.getElementById(id).removeAttribute?.("aria-invalid"));
  const missing = fields.find(id => !document.getElementById(id).value.trim());
  if (missing) {
    terminalStatusKey = "";
    document.getElementById("status").textContent = t("error.empty");
    document.getElementById("status").classList?.add("is-error");
    document.getElementById(missing).setAttribute?.("aria-invalid", "true");
    document.getElementById("results-bar").classList.remove("is-hidden");
    document.getElementById(missing).focus?.();
    return;
  }
  const version = ++searchVersion;
  let run = null;
  try {
    const body = mode() === "pair"
      ? { mode: "pair", first: document.getElementById("first").value, second: document.getElementById("second").value }
      : { mode: "words", query: document.getElementById("query").value };
    const names = sources();
    makePanels(names);
    lastAnswers = names.map(source => {
      const extra = source === "historydb"
        ? { site: historydbSite, item: historydbItem }
        : source === "nahf"
          ? { item: nahfItem }
          : {};
      return { source, answer: null, request: { ...body, ...extra, source, generation: version } };
    });
    run = beginSearchRun(version, lastAnswers);
    setStatusKey("");
    renderAll();
    updateSelectionNotice();
    document.getElementById("results-bar").classList.remove("is-hidden");
    syncSearchButton();
    // Each source answers on its own; a slow one does not hold back the others.
    // A panel rendering failure must not release the guard while another source
    // still has a request in flight.
    // Register every pending token before any bridge call starts so a synchronous
    // bridge throw cannot strand the guard with untracked work.
    const tokens = lastAnswers.map(() => trackPending(run));
    const tasks = lastAnswers.map((entry, index) =>
      receiveSource(run, entry, index, tokens[index]));
    await Promise.allSettled(tasks);
  } catch (error) {
    if (run) run.presentationFailed = true;
    if (run && run.pending.size === 0) maybeFinishRun(run);
    throw error;
  }
});

form.querySelectorAll("input[name=mode]").forEach(r => r.addEventListener("change", updateHint));
// Hovering plays the arrows' dash; leaving with the mouse without pressing plays it in reverse so the arrows
// visibly go back (style.css ".swap.is-returning"). The class is cleared on re-entry, otherwise it would
// override the hover dash, and it is re-added after a reflow so leaving again mid-return restarts the return.
const swapButton = document.getElementById("swap");
const swapMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let swapPressed = false;
swapButton.addEventListener("pointerenter", () => {
  swapPressed = false;
  swapButton.classList.remove("is-returning");
});
swapButton.addEventListener("pointerleave", event => {
  if (event.pointerType !== "mouse" || swapPressed || swapMotion.matches) return;
  swapButton.classList.remove("is-returning");
  void swapButton.offsetWidth;
  swapButton.classList.add("is-returning");
});
swapButton.addEventListener("animationend", event => {
  if (event.target.classList.contains("swap-arrow-bottom")) swapButton.classList.remove("is-returning");
});
swapButton.addEventListener("click", () => {
  swapPressed = true;
  const a = document.getElementById("first");
  const b = document.getElementById("second");
  [a.value, b.value] = [b.value, a.value];
});
document.querySelectorAll(".clear").forEach(button => button.addEventListener("click", () => {
  const input = button.previousElementSibling;
  input.value = "";
  input.focus();
}));

// A failed start leaves every label empty if the catalog never arrived, so the
// error card reuses the catalog when it exists and otherwise shows its keys.
function showStartupError() {
  const box = document.getElementById("startup-error");
  box.classList.remove("is-hidden");
  translate(box);
}
function hideStartupError() {
  document.getElementById("startup-error").classList.add("is-hidden");
}

function languageKeydown(event, code) {
  const keys = ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"];
  if (!keys.includes(event.key)) return;
  event.preventDefault();
  const index = languages.findIndex(item => item.code === code);
  if (index < 0) return;
  let next = index;
  if (event.key === "Home") next = 0;
  else if (event.key === "End") next = languages.length - 1;
  else if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % languages.length;
  else next = (index - 1 + languages.length) % languages.length;
  if (next !== index) {
    const target = [...document.querySelectorAll("#language-list .language-row")].find(item => item.dataset.code === languages[next].code);
    document.querySelectorAll("#language-list .language-row").forEach(item => { item.tabIndex = item === target ? 0 : -1; });
    target?.focus();
  }
}
function languageRow(code, name) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "language-row";
  button.dataset.code = code;
  button.setAttribute("role", "radio");
  button.setAttribute("aria-checked", String(code === currentLanguage));
  button.tabIndex = code === currentLanguage ? 0 : -1;
  const label = document.createElement("span");
  label.className = "language-label";
  label.textContent = name;
  const check = document.createElement("span");
  check.className = "language-check";
  check.setAttribute("aria-hidden", "true");
  check.textContent = code === currentLanguage ? "✓" : "";
  button.append(label, check);
  button.addEventListener("click", () => chooseLanguage(code));
  button.addEventListener("keydown", event => languageKeydown(event, code));
  return button;
}
function updateLanguageList() {
  const list = document.getElementById("language-list");
  if (!list || !languages.length) return;
  const existing = [...list.querySelectorAll(".language-row")];
  if (existing.length !== languages.length) {
    list.replaceChildren(...languages.map(({ code, name }) => languageRow(code, name)));
  }
  languages.forEach(({ code, name }) => {
    const button = [...list.querySelectorAll(".language-row")].find(item => item.dataset.code === code);
    if (!button) return;
    button.setAttribute("aria-checked", String(code === currentLanguage));
    button.tabIndex = code === currentLanguage ? 0 : -1;
    const label = button.querySelector(".language-label");
    if (label) label.textContent = name;
    const check = button.querySelector(".language-check");
    if (check) check.textContent = code === currentLanguage ? "✓" : "";
  });
}
let languageSaveRequestId = 0;
async function chooseLanguage(code) {
  if (code === currentLanguage) return;
  const status = document.getElementById("language-status");
  if (status) {
    status.textContent = "";
    status.classList.remove("is-error");
  }
  const saveReqId = ++languageSaveRequestId;
  try {
    const applied = await setLanguage(code);
    if (!applied || saveReqId !== languageSaveRequestId) return;
    hideStartupError();
    const button = [...document.querySelectorAll("#language-list .language-row")]
      .find(item => item.dataset.code === code);
    button?.focus();
    const saved = await api().save_language(code);
    if (saveReqId !== languageSaveRequestId) return;
    if (!saved) throw new Error("language preference could not be saved");
  } catch (error) {
    console.error(error);
    if (saveReqId === languageSaveRequestId) {
      if (status) {
        status.classList.add("is-error");
        status.textContent = t("error.network");
      } else {
        showStartupError();
      }
    }
  }
}
async function loadHomeIcon() {
  try {
    const icon = document.querySelector(".brand-icon");
    if (icon) icon.src = await api().home_icon();
  } catch (error) {
    // A decorative image must not prevent the catalog and text library loading.
    console.error(error);
  }
}
async function initialize() {
  const [available, saved] = await Promise.all([api().languages(), api().saved_language()]);
  languages = available;
  if (!languages.length) throw new Error("no interface language");
  const browser = navigator.language || "en";
  const guess = languages.find(l => browser.startsWith(l.code))?.code
    ?? (browser.startsWith("zh") ? (/TW|HK|Hant/.test(browser) ? "zh-Hant" : "zh-Hans") : "en");
  currentLanguage = saved && languages.some(l => l.code === saved) ? saved : guess;
  updateLanguageList();
  // Load the catalog first: a later failure can then word its error in the chosen
  // language instead of leaving every label empty.
  await setLanguage(currentLanguage);
  void restoreSort();
  void loadHomeIcon();
  const [generation, loaded, locals, options] = await Promise.all([
    api().search_generation(), api().library(), api().local_libraries(), api().source_options(),
  ]);
  searchVersion = Math.max(searchVersion, generation);
  library = loaded;
  localState = locals;
  applySourceOptions(options);
  restoreSources();
  showLibrary();
  updateSource();
  if (currentView === "sources") autoLoadSavedSlots();
  if (currentView === "import") refreshHistoryAvailability();
}

let starting = false;
async function start() {
  if (starting) return;
  starting = true;
  const retry = document.getElementById("startup-retry");
  retry.disabled = true;
  try {
    await initialize();
    hideStartupError();
  } catch (error) {
    console.error(error);
    showStartupError();
  } finally {
    starting = false;
    retry.disabled = false;
  }
}
document.getElementById("startup-retry").addEventListener("click", start);
// The bridge may be ready before or after this script runs.
if (window.pywebview && window.pywebview.api && window.pywebview.api.languages) start();
else window.addEventListener("pywebviewready", start, { once: true });
