// Search window behaviour. Every visible word comes from the catalog of the chosen
// language (AGENTS.md "Language"); source text is shown exactly as the server sends it,
// whatever the language, so choosing zh-Hans never converts characters.
"use strict";

const PAGE = 100; // cards rendered per group before "show more"
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

function t(key, vars = {}) {
  const text = catalog[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
}

function numberText(value) {
  return Number(value).toLocaleString(document.documentElement?.lang || "en");
}

// Catalog text for every marked element under root (the page, or a new panel).
function translate(root) {
  root.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-aria]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
  root.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  root.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
}

function applyCatalog() {
  translate(document);
  updateHint();
  showLibrary();
  updateSource();
  renderAll();
  updateExternalProvider();
  updateExternal();
  updateLanguageList();
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
function viewElement(name) { return document.getElementById(`view-${name}`); }
function showView(name, { focus = true } = {}) {
  if (!VIEW_NAMES.includes(name)) return;
  // A native dialog owns Escape and focus while it is open. Do not move the
  // screen underneath it.
  if (document.querySelector("dialog[open]") && name !== currentView) return;
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
  if (event.key !== "Escape" || document.querySelector("dialog[open]")) return;
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
  return LOCAL_SOURCES.includes(code) ? localLabel(code) : t(`source.${code}`);
}
function showLocalControls() {
  const busy = isLocalBusy() || localState.busy || savingSources;
  localRows.forEach(({ code, row, title, path, detail, load, choose, remove, skipped, skippedText }) => {
    const info = localInfo(code);
    row.classList.toggle("is-hidden", !info?.configured);
    title.textContent = localLabel(code);
    path.textContent = info?.folder || t("local.unset");
    detail.textContent = info?.load_error
      ? t(`error.${info.load_error}`, { path: info.folder })
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
    load.textContent = t(info?.load_error ? "sources.retry" : "local.load");
    load.classList.toggle("is-hidden", !canRetry);
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
  await removeLocal(code);
});
removeDialog.addEventListener("close", () => { pendingRemove = ""; });
async function removeLocal(code) {
  const task = beginLocalBusy();
  try {
    const answer = await api().remove_local(code);
    if (answer.error) { localError(answer); return; }
    localState = answer;
    autoLoadTried.delete(code);
    loadingSlots.delete(code);
    library = await api().library();
    clearResults();
    refreshHistoryAvailability();
  } catch { localError({ error: "network" }); }
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
function localError(answer, statusId = "local-status") {
  const status = document.getElementById(statusId);
  status.textContent = t(`error.${answer.error}`, answer);
  const section = status.closest("details");
  if (section) section.open = true;
  status.scrollIntoView({ block: "nearest" });
}
function settingsError(answer) {
  const status = document.getElementById("settings-status");
  status.textContent = t(`error.${answer.error}`, answer);
}
async function saveSources() {
  if (savingSources) return;
  savingSources = true; updateSource();
  try {
    const answer = await api().set_sources(sources());
    if (answer.error) { localError(answer); restoreSources(); }
    else localState.selected = answer.sources;
  } catch { localError({ error: "network" }); restoreSources(); }
  finally { savingSources = false; updateSource(); }
}
function restoreSources() {
  allSourceBoxes().forEach(box => { box.checked = localState.selected.includes(box.value); });
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
  autoLoadTried.add(code);
  if (!auto) autoLoadTried.delete(code);
  loadingSlots.add(code);
  const task = beginLocalBusy();
  document.getElementById("local-status").textContent = "";
  try {
    const answer = await api().load_local(code);
    if (answer.error) {
      localError(answer);
      try { localState = await api().local_libraries(); } catch {}
    } else {
      localState = answer;
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
      status.textContent = t(`error.${answer.error}`, answer);
    } else {
      if (answer.library?.error) {
        status.classList.add("is-error");
        status.textContent = t("ctp.done", answer.imported) + " " + t("import.reload_failed") + " " + t(`error.${answer.library.error}`, answer.library);
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
  errEl.textContent = t(`error.${answer.error}`, answer);
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
  status.textContent = t(`error.${answer.error}`, answer);
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
  if (link) api().open_link(link);
});
const externalRevisionLink = document.getElementById("external-revision-link");
if (externalRevisionLink) {
  externalRevisionLink.addEventListener("click", event => {
    event.preventDefault();
    const link = event.currentTarget.getAttribute("href");
    if (link) api().open_link(link);
  });
}
document.getElementById("ctp-find-link").addEventListener("click", event => {
  event.preventDefault();
  const link = event.currentTarget.getAttribute("href");
  if (link) api().open_link(link);
});
externalDialog.addEventListener("keydown", event => {
  if (event.key !== "Tab") return;
  const focusable = [...externalDialog.querySelectorAll('button:not(:disabled), select:not(:disabled), input:not(:disabled), summary, textarea, a[href]')]
    .filter(el => el.getClientRects().length && !el.disabled && el.tabIndex >= 0);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey) {
    if (document.activeElement === first || !externalDialog.contains(document.activeElement)) {
      event.preventDefault();
      last.focus();
      last.scrollIntoView({ block: "nearest" });
    }
  } else {
    if (document.activeElement === last || !externalDialog.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
      first.scrollIntoView({ block: "nearest" });
    }
  }
});
externalDialog.addEventListener("focusin", event => {
  if (event.target && typeof event.target.scrollIntoView === "function") {
    event.target.scrollIntoView({ block: "nearest" });
  }
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
document.getElementById("external-source").addEventListener("click", event => { event.preventDefault(); const link = event.currentTarget.getAttribute("href"); if (link) api().open_link(link); });
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
    else if (answer.library) externalError(answer.library);
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
  if (book.error) { status.textContent = t(`error.${book.error}`); return; }
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
      link.addEventListener("click", event => { event.preventDefault(); api().open_link(download.url); });
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
    if (answer.error) { status.textContent = t(`error.${answer.error}`); return; }
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
    page.addEventListener("click", event => { event.preventDefault(); api().open_link(d.page); });
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
  try { await runReleaseTask(taskText, call, name); }
  finally { endLocalBusy(task); }
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
    status.textContent = t(`error.${answer.error}`, answer);
    syncResultsBar();
    return;
  }
  // The texts are saved even when the folder cannot be read afterwards; that is
  // said as an error, and the library on screen stays as it was.
  const unopened = answer.library && answer.library.error ? answer.library : null;
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
    status.textContent = t("release.unopened", unopened) + " " + t(`error.${unopened.error}`, unopened);
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
  if (item.status === "failed") lines.push(t(`error.${item.code}`, item));
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
// Adding a source here adds its chip and its footer link.
const SOURCES = ["local", "itkc", "ndl", "cbeta", "cyberseodang", "docusky"];
const ONLINE = SOURCES.filter(code => code !== "local");

// Several sources can be searched at once, each in its own panel (at most four: 田).
const MAX_SOURCES = 4;
const sourceChips = document.getElementById("source-chips");
const sourceBoxes = ONLINE.map(code => {
  const label = document.createElement("label");
  label.className = "chip";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.value = code;
  box.checked = false;
  const name = document.createElement("span");
  name.dataset.i18n = `source.${code}`;
  label.append(box, " ", name);
  sourceChips.appendChild(label);
  return box;
});

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

const dialog = document.getElementById("source-dialog");
const sourceNotes = document.getElementById("source-notes-list");
ONLINE.forEach(code => {
  const link = document.createElement("button");
  link.type = "button";
  link.className = "source-link";
  link.dataset.i18n = `source.${code}`;
  link.addEventListener("click", () => {
    dialog.querySelector(".dialog-title").textContent = t(`source.${code}`);
    dialog.querySelector(".dialog-body").textContent = t(`source.${code}.note`);
    dialog.showModal();
  });
  sourceNotes.appendChild(link);
});
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
  const welcome = document.getElementById("welcome");
  welcome.classList.toggle("is-hidden", hasUsableSources());
}
function updateSource() {
  const chosen = sources();
  allSourceBoxes().forEach(box => {
    const missing = LOCAL_SOURCES.includes(box.value) && !selectedLocalReady(box.value);
    if (missing && box.checked) box.checked = false;
    box.disabled = savingSources || missing || (!box.checked && chosen.length >= MAX_SOURCES);
  });
  showLocalControls();
  updateWelcome();
  syncSearchButton();
}
const homeButton = document.getElementById("home");
homeButton.addEventListener("click", () => showView("search"));
document.querySelectorAll(".top-nav [data-view]").forEach(button => {
  button.addEventListener("click", () => showView(button.dataset.view));
});
document.getElementById("welcome-sources").addEventListener("click", () => showView("sources"));
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
    const space = `${Math.ceil(topEl.getBoundingClientRect().height) + 12}px`;
    const style = document.documentElement.style;
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
  clearResults();
  await saveSources();
}));
let searchVersion = 0;
// One search at a time: the bridge calls cannot be cancelled, and a second run
// would spend another source's request budget while the first still answers.
let searchBusy = false;
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
  button.disabled = searchBusy || !hasUsableSources();
  form.setAttribute("aria-busy", String(searchBusy));
  syncResultsBar();
}
function clearResults() {
  searchVersion++;
  api().invalidate_searches?.(searchVersion)?.catch(() => {});
  resultMotion.reset();
  lastAnswers = [];
  document.getElementById("status").textContent = searchBusy ? t("results.searching") : "";
  document.getElementById("panels").replaceChildren();
  updateWelcome();
  syncSearchButton();
}
document.getElementById("results-clear").addEventListener("click", clearResults);
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

function card(row, number) {
  const node = document.getElementById("card").content.firstElementChild.cloneNode(true);
  // The position in its group, so a result can be found again by number.
  node.dataset.number = String(number);
  node.querySelector(".num").textContent = String(number);
  const src = node.querySelector(".src");
  src.textContent = row.title;
  if (row.link) {
    // Opens the source's own page in the system browser, not inside the window.
    src.dataset.link = row.link;
    src.title = row.link;
    src.addEventListener("click", event => { event.preventDefault(); api().open_link(row.link); });
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
  return node;
}

function fill(section, rows, labelKey) {
  const list = section.querySelector(".list");
  const remembered = Number(section.dataset.shown || PAGE);
  const scroll = list.scrollTop;
  resultMotion.forget(list);
  list.replaceChildren();
  section.querySelector(".label").textContent = t(labelKey);
  section.querySelector(".n").textContent = t("results.count", { n: rows.length });
  section.classList.toggle("is-hidden", rows.length === 0 && section.classList.contains("less"));
  if (!rows.length) {
    section.querySelector(".jump").hidden = true;
    const empty = document.createElement("p");
    empty.className = "card none";
    empty.textContent = t("results.none");
    list.appendChild(empty);
    return;
  }
  let shown = 0;
  const more = document.createElement("button");
  more.type = "button";
  more.className = "more";
  const next = () => {
    rows.slice(shown, shown + PAGE).forEach((row, i) => {
      const node = card(row, shown + i + 1);
      list.insertBefore(node, more);
      resultMotion.observe(node, row, i < 6 ? i * 20 : 0);
    });
    shown = Math.min(shown + PAGE, rows.length);
    section.dataset.shown = String(shown);
    more.textContent = t("results.more", { n: Math.min(PAGE, rows.length - shown) });
    more.hidden = shown >= rows.length;
  };
  list.appendChild(more);
  more.addEventListener("click", next);
  next();
  while (shown < Math.min(remembered, rows.length)) next();
  list.scrollTop = scroll;
  setupJump(section, rows.length, number => {
    // Render up to the asked card first: groups show PAGE cards at a time.
    while (shown < number) next();
    const target = list.querySelector(`.card[data-number="${number}"]`);
    if (!target) return;
    // Measured on screen (offsetTop depends on which ancestor is positioned), and
    // again once newly rendered cards and late fonts have settled their heights.
    const align = () => { list.scrollTop += target.getBoundingClientRect().top - list.getBoundingClientRect().top; };
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
    const number = Math.min(Math.max(Math.trunc(Number(box.value)), 1), total);
    if (!Number.isFinite(number) || number < 1) return;
    box.value = String(number);
    // A closed group hides its list, so scrolling it would move an invisible node
    // and the requested result would never appear. Open the group before `go`.
    section.classList.remove("is-closed");
    section.querySelector(".group-toggle").setAttribute("aria-expanded", "true");
    go(number);
  };
  box.onchange = jump;
  box.onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); jump(); } };
  // Typing in the box must not fold the group.
  box.onclick = event => event.stopPropagation();
}

// A year as text; years before the common era are negative in the data.
function yearText(value) {
  if (value === null || value === undefined) return "";
  return value < 0 ? t("era.bce", { n: -value }) : String(value);
}
// When a result dates from, as its source records it (cpe.gui.service rows: era).
function eraText(era) {
  if (!era) return "";
  const year = value => yearText(value) || "?";
  if (era.kind === "author_life") return t("era.author_life", { from: year(era.from), to: year(era.to) });
  if (era.kind === "published") return t("era.published", { year: era.year, text: era.text });
  if (era.kind === "dynasty") return t("era.dynasty", { dynasty: era.dynasty, from: year(era.from), to: year(era.to) });
  if (era.kind === "manifest") {
    // The researcher's own label and years, shown as written: no catalog words.
    const years = yearText(era.from) && (yearText(era.to) && era.to !== era.from
      ? `${yearText(era.from)}–${yearText(era.to)}` : yearText(era.from));
    return [era.label, years].filter(Boolean).join(" ");
  }
  return "";
}
function sorted(rows) {
  if (document.getElementById("sort").value !== "era") return rows;
  // Oldest first; results without a date keep their order at the end.
  const key = row => (row.era && Number.isFinite(row.era.year) ? row.era.year : Infinity);
  return rows.map((row, index) => [row, index]).sort((a, b) => key(a[0]) - key(b[0]) || a[1] - b[1]).map(pair => pair[0]);
}
document.getElementById("sort").addEventListener("change", () => renderAll());

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
  if (info.name === "docusky") return t("source.docusky.summary", info);
  return "";
}

let lastAnswers = []; // [{source, answer}] in panel order; answer is null while searching
function makePanels(names) {
  resultMotion.reset();
  document.getElementById("welcome").classList.add("is-hidden");
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
        const closed = group.classList.toggle("is-closed");
        toggle.setAttribute("aria-expanded", String(!closed));
      });
    });
    resultMotion.observe(panel);
    return panel;
  }));
}
function renderPanel(panel, name, answer) {
  panel.querySelector(".panel-title").textContent = sourceName(name);
  const status = panel.querySelector(".panel-status");
  const likely = panel.querySelector(".likely");
  const less = panel.querySelector(".less");
  status.classList.toggle("is-error", Boolean(answer && answer.error));
  panel.classList.toggle("is-loading", !answer);
  if (!answer) {
    status.textContent = t("results.searching");
    likely.classList.add("is-hidden");
    less.classList.add("is-hidden");
    return;
  }
  if (answer.error) {
    status.textContent = t(`error.${answer.error}`, answer);
    likely.classList.add("is-hidden");
    less.classList.add("is-hidden");
    return;
  }
  status.textContent = LOCAL_SOURCES.includes(name)
    ? t("library.folder", { n: numberText(textCount(localInfo(name))), folder: localInfo(name)?.folder || "" })
    : sourceSummary(answer.source);
  likely.classList.remove("is-hidden");
  let likelyLabel = answer.mode === "pair" ? "results.likely" : "results.all";
  let lessLabel = "results.less";
  if (name === "cyberseodang") {
    likelyLabel = "source.cyberseodang.checked";
    lessLabel = "source.cyberseodang.unchecked";
    if (!panel.dataset.received) less.classList.remove("is-closed");
  }
  panel.querySelectorAll(".group").forEach(group => {
    group.querySelector(".group-toggle").setAttribute("aria-expanded", String(!group.classList.contains("is-closed")));
  });
  panel.dataset.received = "true";
  fill(likely, sorted(answer.likely), likelyLabel);
  fill(less, sorted(answer.less), lessLabel);
}
function renderAll() {
  const panels = [...document.querySelectorAll("#panels > .panel")];
  lastAnswers.forEach(({ source, answer }, index) => {
    if (panels[index]) renderPanel(panels[index], source, answer);
  });
}

form.addEventListener("keydown", event => {
  if (event.key !== "Enter" || event.target.tagName !== "INPUT") return;
  // IME composition confirms with Enter; do not submit the search mid-composition.
  if (event.isComposing || event.keyCode === 229) event.preventDefault();
});
form.addEventListener("submit", async event => {
  event.preventDefault();
  if (searchBusy) return;
  if (!hasUsableSources()) {
    showView("sources");
    return;
  }
  searchBusy = true;
  syncSearchButton();
  const version = ++searchVersion;
  try {
    const body = mode() === "pair"
      ? { mode: "pair", first: document.getElementById("first").value, second: document.getElementById("second").value }
      : { mode: "words", query: document.getElementById("query").value };
    const names = sources();
    document.getElementById("status").textContent = "";
    makePanels(names);
    lastAnswers = names.map(source => ({ source, answer: null }));
    renderAll();
    document.getElementById("results-bar").classList.remove("is-hidden");
    // Each source answers on its own; a slow one does not hold back the others.
    // A panel rendering failure must not release the guard while another source
    // still has a request in flight.
    await Promise.allSettled(names.map(async (source, index) => {
      let answer;
      try {
        answer = await api().search({ ...body, source, generation: version });
      } catch {
        answer = { error: "network" };
      }
      if (version !== searchVersion) return;
      // A first search may have loaded this saved slot; refresh its count before
      // the panel words the source line.
      if (LOCAL_SOURCES.includes(source)) {
        try {
          localState = await api().local_libraries();
          updateSource();
        } catch {
          // Keep the counts already on screen; the search answer is still valid.
        }
      }
      lastAnswers[index] = { source, answer };
      const panel = document.querySelectorAll("#panels > .panel")[index];
      if (panel) renderPanel(panel, source, answer);
    }));
  } finally {
    searchBusy = false;
    syncSearchButton();
    // A cleared or replaced search leaves no panel to report its own end.
    if (version !== searchVersion) document.getElementById("status").textContent = "";
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
  if (next !== index) chooseLanguage(languages[next].code);
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
  languages = await api().languages();
  if (!languages.length) throw new Error("no interface language");
  // The language chosen last time; the first start follows the OS language.
  const saved = await api().saved_language();
  const browser = navigator.language || "en";
  const guess = languages.find(l => browser.startsWith(l.code))?.code
    ?? (browser.startsWith("zh") ? (/TW|HK|Hant/.test(browser) ? "zh-Hant" : "zh-Hans") : "en");
  currentLanguage = saved && languages.some(l => l.code === saved) ? saved : guess;
  updateLanguageList();
  // Load the catalog first: a later failure can then word its error in the chosen
  // language instead of leaving every label empty.
  await setLanguage(currentLanguage);
  void loadHomeIcon();
  library = await api().library();
  localState = await api().local_libraries();
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
