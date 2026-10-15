// Search window behaviour. Every visible word comes from the catalog of the chosen
// language (AGENTS.md "Language"); source text is shown exactly as the server sends it,
// whatever the language, so choosing zh-Hans never converts characters.
"use strict";

const PAGE = 100; // cards rendered per group before "show more"
let catalog = {};

function t(key, vars = {}) {
  const text = catalog[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
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
}

// The window's in-process bridge to cpe.gui.app.Api (no network involved).
function api() { return window.pywebview.api; }

async function setLanguage(code) {
  catalog = await api().catalog(code);
  document.documentElement.lang = code;
  applyCatalog();
  api().set_title(t("app.title"));
}

let library = { texts: [], folder: "", skipped: [] };
function showLibrary() {
  const el = document.getElementById("library");
  const n = library.texts.length.toLocaleString();
  if (!library.texts.length) el.textContent = t("library.empty");
  else if (library.folder) el.textContent = t("library.folder", { n, folder: library.folder });
  else el.textContent = t("library.count", { n });
  const skipped = document.getElementById("skipped");
  const files = library.skipped || [];
  skipped.classList.toggle("is-hidden", files.length === 0);
  skipped.textContent = files.length
    ? t("library.skipped", { n: files.length, files: files.slice(0, 5).join(", ") + (files.length > 5 ? " …" : "") })
    : "";
  renderSummary();
}

document.getElementById("open").addEventListener("click", async () => {
  const answer = await api().choose_folder();
  if (answer.cancelled) return;
  if (answer.error) {
    document.getElementById("status").textContent = t(`error.${answer.error}`, answer);
    return;
  }
  library = answer;
  showLibrary();
  // Results of the previous folder no longer apply.
  clearResults();
});

// Import a work from the Chinese Text Project into the open folder (cpe.ctp.adapter):
// CTP has no search API, so its texts are saved and searched as local text.
const ctpDialog = document.getElementById("ctp-dialog");
async function showCtpKeyState() {
  document.getElementById("ctp-key-state").textContent =
    t(await api().ctp_key_set() ? "ctp.key.stored" : "ctp.key.missing");
}
document.getElementById("ctp-open").addEventListener("click", async () => {
  document.getElementById("ctp-status").textContent = "";
  await showCtpKeyState();
  ctpDialog.showModal();
});
document.getElementById("ctp-key-save").addEventListener("click", async () => {
  const input = document.getElementById("ctp-key");
  await api().save_ctp_key(input.value);
  input.value = "";
  await showCtpKeyState();
});
document.getElementById("ctp-import").addEventListener("click", async () => {
  const status = document.getElementById("ctp-status");
  const button = document.getElementById("ctp-import");
  button.disabled = true;
  status.classList.remove("is-error");
  status.textContent = t("ctp.working");
  try {
    const answer = await api().import_ctp(document.getElementById("ctp-url").value);
    if (answer.error) {
      status.classList.add("is-error");
      status.textContent = t(`error.${answer.error}`, answer);
    } else {
      status.textContent = t("ctp.done", answer.imported);
      if (answer.library && !answer.library.error) {
        library = answer.library;
        showLibrary();
        clearResults();
      }
    }
  } catch {
    status.classList.add("is-error");
    status.textContent = t("error.network");
  } finally {
    button.disabled = false;
  }
});

// Institute original-text releases (cpe.local.portal, cpe.local.releases): the dialog
// asks for the institute, the release and the folder, in that order. The download and
// conversion run on the bridge's own thread, so the window stays usable while the
// bottom left line shows the work is running.
const INSTITUTES = ["nikh", "itkc"];
const releaseDialog = document.getElementById("release-dialog");
const release = { datasets: [], institute: "", pk: "", folder: "" };

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
  document.getElementById("release-folder").textContent =
    release.folder ? t("release.folder.current", { folder: release.folder }) : t("release.folder.none");
  document.getElementById("release-start").disabled = !(release.pk && release.folder);
}
document.getElementById("release-open").addEventListener("click", async () => {
  if (!release.datasets.length) release.datasets = await api().datasets();
  release.folder = release.folder || library.folder || "";
  renderInstitutes();
  renderDatasets();
  updateRelease();
  releaseDialog.showModal();
});
document.getElementById("release-folder-pick").addEventListener("click", async () => {
  const answer = await api().pick_folder();
  if (answer.folder) { release.folder = answer.folder; updateRelease(); }
});
async function runRelease(taskText, call, name) {
  const status = document.getElementById("status");
  const task = document.getElementById("task");
  const opener = document.getElementById("release-open");
  status.classList.remove("is-error");
  status.textContent = "";
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
    opener.disabled = false;
  }
  if (answer.cancelled) return;
  if (answer.error) {
    status.classList.add("is-error");
    status.textContent = t(`error.${answer.error}`, answer);
    return;
  }
  if (answer.library && !answer.library.error) {
    library = answer.library;
    showLibrary();
    clearResults();
  }
  status.textContent = t("release.done", { ...answer.imported, book: name || answer.imported.book });
}
document.getElementById("release-start").addEventListener("click", () => {
  const { pk, folder } = release;
  const name = t(`release.ds.${pk}`);
  releaseDialog.close();
  settingsDialog.close();
  runRelease(t("release.background.download", { name }), () => api().import_dataset(pk, folder), name);
});
document.getElementById("release-file").addEventListener("click", () => {
  const folder = release.folder;
  releaseDialog.close();
  settingsDialog.close();
  runRelease(t("release.background"), () => api().import_release(t("release.filetype"), folder), "");
});

const form = document.getElementById("search");
function mode() { return form.querySelector("input[name=mode]:checked").value; }
// Every searchable source, in menu order. "local" is the text folder; the others are
// online and each has a catalog name source.<code> and an explanation source.<code>.note.
// Adding a source here adds its chip and its footer link.
const SOURCES = ["local", "itkc", "ndl", "cbeta"];
const ONLINE = SOURCES.filter(code => code !== "local");

// Several sources can be searched at once, each in its own panel (at most four: 田).
const MAX_SOURCES = 4;
const sourceChips = document.getElementById("source-chips");
const sourceBoxes = SOURCES.map(code => {
  const label = document.createElement("label");
  label.className = "chip";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.value = code;
  box.checked = code === "local";
  const name = document.createElement("span");
  name.dataset.i18n = `source.${code}`;
  label.append(box, " ", name);
  sourceChips.appendChild(label);
  return box;
});

const links = document.getElementById("source-links");
const dialog = document.getElementById("source-dialog");
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
  links.appendChild(link);
});
function sources() { return sourceBoxes.filter(box => box.checked).map(box => box.value); }
function updateSource() {
  const chosen = sources();
  const local = chosen.includes("local");
  document.getElementById("local-library").classList.toggle("is-hidden", !local);
  document.getElementById("skipped").classList.toggle("is-hidden", !local || !(library.skipped || []).length);
  // Keep at least one source, and no more than the grid holds.
  sourceBoxes.forEach(box => { box.disabled = !box.checked && chosen.length >= MAX_SOURCES; });
  renderSummary();
}
// The main screen's one line: which sources a search covers, and how many local texts.
function renderSummary() {
  const names = sources().map(code => code === "local"
    ? t("summary.local", { n: library.texts.length.toLocaleString() })
    : t(`source.${code}`));
  document.getElementById("summary").textContent = t("summary.sources", { names: names.join(" · ") });
}
const settingsDialog = document.getElementById("settings-dialog");
document.getElementById("settings-open").addEventListener("click", () => settingsDialog.showModal());
document.getElementById("summary").addEventListener("click", () => settingsDialog.showModal());
sourceBoxes.forEach(box => box.addEventListener("change", () => {
  if (!sources().length) box.checked = true;
  updateSource();
  clearResults();
}));
function clearResults() {
  lastAnswers = [];
  document.getElementById("status").textContent = "";
  document.getElementById("panels").replaceChildren();
}
function updateHint() {
  form.classList.toggle("is-compound", mode() === "pair");
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
  list.replaceChildren();
  section.querySelector(".label").textContent = t(labelKey);
  section.querySelector(".n").textContent = t("results.count", { n: rows.length });
  section.classList.toggle("is-hidden", rows.length === 0 && section.classList.contains("less"));
  if (!rows.length) {
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
    rows.slice(shown, shown + PAGE).forEach((row, i) => list.insertBefore(card(row, shown + i + 1), more));
    shown = Math.min(shown + PAGE, rows.length);
    more.textContent = t("results.more", { n: Math.min(PAGE, rows.length - shown) });
    more.hidden = shown >= rows.length;
  };
  list.appendChild(more);
  more.addEventListener("click", next);
  next();
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
    if (Number.isFinite(number) && number >= 1) { box.value = String(number); go(number); }
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
  return "";
}

let lastAnswers = []; // [{source, answer}] in panel order; answer is null while searching
function makePanels(names) {
  const box = document.getElementById("panels");
  box.className = `panels n${names.length}`;
  box.replaceChildren(...names.map(name => {
    const panel = document.getElementById("panel").content.firstElementChild.cloneNode(true);
    panel.dataset.source = name;
    translate(panel);
    panel.querySelectorAll(".group > h3").forEach(h => h.addEventListener("click", () => {
      h.parentElement.classList.toggle("is-closed");
    }));
    return panel;
  }));
}
function renderPanel(panel, name, answer) {
  panel.querySelector(".panel-title").textContent = t(`source.${name}`);
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
  status.textContent = sourceSummary(answer.source);
  likely.classList.remove("is-hidden");
  fill(likely, sorted(answer.likely), answer.mode === "pair" ? "results.likely" : "results.all");
  fill(less, sorted(answer.less), "results.less");
}
function renderAll() {
  const panels = [...document.querySelectorAll("#panels > .panel")];
  lastAnswers.forEach(({ source, answer }, index) => {
    if (panels[index]) renderPanel(panels[index], source, answer);
  });
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const body = mode() === "pair"
    ? { mode: "pair", first: document.getElementById("first").value, second: document.getElementById("second").value }
    : { mode: "words", query: document.getElementById("query").value };
  const names = sources();
  const button = form.querySelector(".go");
  button.disabled = true;
  document.getElementById("status").textContent = "";
  makePanels(names);
  lastAnswers = names.map(source => ({ source, answer: null }));
  renderAll();
  // Each source answers on its own; a slow one does not hold back the others.
  await Promise.all(names.map(async (source, index) => {
    let answer;
    try {
      answer = await api().search({ ...body, source });
    } catch {
      answer = { error: "network" };
    }
    lastAnswers[index] = { source, answer };
    const panel = document.querySelectorAll("#panels > .panel")[index];
    if (panel) renderPanel(panel, source, answer);
  }));
  button.disabled = false;
});

form.querySelectorAll("input[name=mode]").forEach(r => r.addEventListener("change", updateHint));
document.getElementById("swap").addEventListener("click", () => {
  const a = document.getElementById("first");
  const b = document.getElementById("second");
  [a.value, b.value] = [b.value, a.value];
});
document.querySelectorAll(".clear").forEach(button => button.addEventListener("click", () => {
  const input = button.previousElementSibling;
  input.value = "";
  input.focus();
}));

async function start() {
  const languages = await api().languages();
  const select = document.getElementById("language");
  languages.forEach(({ code, name }) => select.appendChild(new Option(name, code)));
  // The language chosen last time; the first start follows the OS language.
  const saved = await api().saved_language();
  const browser = navigator.language || "en";
  const guess = languages.find(l => browser.startsWith(l.code))?.code
    ?? (browser.startsWith("zh") ? (/TW|HK|Hant/.test(browser) ? "zh-Hant" : "zh-Hans") : "en");
  select.value = saved && languages.some(l => l.code === saved) ? saved : guess;
  select.addEventListener("change", () => {
    api().save_language(select.value);
    setLanguage(select.value);
  });
  library = await api().library();
  await setLanguage(select.value);
}
// The bridge may be ready before or after this script runs.
if (window.pywebview && window.pywebview.api && window.pywebview.api.languages) start();
else window.addEventListener("pywebviewready", start, { once: true });
