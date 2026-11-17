// @ts-check
"use strict";

/** @type {StudioState} */
const state = {
  boot: /** @type {BootData} */ (/** @type {unknown} */ (null)), // set by init()
  tracks: [],
  current: null,
  model: {},
  dropFiles: [],
};

/**
 * querySelector for elements that always exist in index.html. The element type
 * is asserted, not checked, and inferred from the target: annotate the variable
 * (`/** @type {HTMLInputElement} *\/ const x = $("#id")`) to get a subtype.
 * @template {Element} [T=HTMLElement]
 * @param {string} sel
 * @returns {T}
 */
const $ = (sel) => /** @type {T} */ (document.querySelector(sel));

/**
 * querySelector within `root` for markup this file just created.
 * @template {Element} [T=HTMLElement]
 * @param {ParentNode} root
 * @param {string} sel
 * @returns {T}
 */
const pick = (root, sel) => /** @type {T} */ (root.querySelector(sel));

/**
 * The selected track. Only called from handlers that run while one is open.
 * @returns {TrackDetail}
 */
function currentTrack() {
  if (!state.current) throw new Error("no track selected");
  return state.current;
}

/**
 * Fetch a studio API route and parse its JSON. Callers annotate the result with
 * the matching response type (see vp/ui/types/studio.d.ts).
 * @param {string} path
 * @param {Omit<RequestInit, "body"> & { body?: string | FormData }} [opts]
 * @returns {Promise<any>}
 */
async function api(path, opts = {}) {
  /** @type {Record<string, string>} */
  const headers = {};
  let body = opts.body;
  if (opts.body instanceof FormData) {
    body = opts.body; // let the browser set the multipart boundary
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...opts, headers, body });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (_) { /* not json */ }
    throw new Error(detail);
  }
  return res.json();
}

/**
 * @param {string} msg
 * @param {"ok" | "error"} [kind]
 */
function toast(msg, kind = "ok") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast ${kind}`;
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 2200);
}

/**
 * @param {boolean} busy
 * @param {string} [msg]
 */
function setBusy(busy, msg = "") {
  $("#status").textContent = msg;
  $("#status").classList.toggle("busy", busy);
}

// Load a video and start playback once the media is ready. Calling play()
// immediately after load() rejects before metadata arrives, which whisked
// renders that wouldn't autoplay; this retries on canplay/loadeddata.
/**
 * @param {HTMLVideoElement} video
 * @param {string} src
 */
function playVideo(video, src) {
  video.onloadeddata = null;
  video.oncanplay = null;
  const go = () => video.play().catch(() => {});
  video.onloadeddata = go;
  video.oncanplay = go;
  video.src = src;
  video.load();
  go();
}

/**
 * One editor control for a style key, built from its STYLE_FIELDS spec.
 * @param {string} key
 * @param {StyleFieldSpec} spec
 * @param {StyleValue} value
 * @returns {HTMLDivElement}
 */
function makeControl(key, spec, value) {
  const el = document.createElement("div");
  el.className = "control";
  const hint = spec.help ? `<span class="hint">${spec.help}</span>` : "";
  el.innerHTML = `<header><label>${key}${hint}</label><output></output></header>`;
  const out = pick(el, "output");
  /** @param {StyleValue} v */
  const onChange = (v) => {
    state.model[key] = v;
    out.textContent = v === null ? "null" : String(v);
    schedulePreview();
    scheduleStyleSave();
  };

  if (spec.type === "bool") {
    el.innerHTML = `<div class="row"><label>${key}${hint}</label><input type="checkbox"></div>`;
    /** @type {HTMLInputElement} */
    const cb = pick(el, "input");
    cb.checked = !!value;
    cb.addEventListener("change", () => onChange(cb.checked));
  } else if (spec.type === "int" || spec.type === "float") {
    const min = spec.min ?? 0;
    const max =
      spec.max ??
      (spec.type === "int"
        ? Math.max(16, Math.ceil((Number(value) || 0) * 2))
        : 1);
    const step = spec.step ?? (spec.type === "int" ? 1 : 0.01);
    el.insertAdjacentHTML(
      "beforeend",
      `<div class="slider-group"><input type="range" min="${min}" max="${max}" step="${step}"></div>`
    );
    /** @type {HTMLInputElement} */
    const slider = pick(el, "input[type=range]");
    slider.value = String(value ?? min);
    out.textContent = String(slider.value);
    const read = () =>
      spec.type === "int" ? parseInt(slider.value, 10) : parseFloat(slider.value);
    slider.addEventListener("input", () => onChange(read()));
  } else if (spec.type === "choice") {
    const choices = spec.choices ?? [];
    const numeric = choices.length && typeof choices[0] === "number";
    el.innerHTML =
      `<header><label>${key}${hint}</label><output></output></header>` +
      `<select>${choices.map((c) => `<option value="${c}">${c}</option>`).join("")}</select>`;
    /** @type {HTMLSelectElement} */
    const sel = pick(el, "select");
    sel.value = String(value);
    out.textContent = String(value);
    sel.addEventListener("change", () =>
      onChange(numeric ? Number(sel.value) : sel.value)
    );
  } else if (spec.type === "preset") {
    el.innerHTML =
      `<header><label>${key}${hint}</label><output></output></header>` +
      `<select></select>`;
    /** @type {HTMLSelectElement} */
    const sel = pick(el, "select");
    for (const name of state.boot.presets || []) {
      sel.add(new Option(name, name));
    }
    const current = value == null ? "clean" : String(value);
    if (![...sel.options].some((option) => option.value === current)) {
      sel.add(new Option(`Custom: ${current}`, current));
    }
    sel.value = current;
    out.textContent = current;
    sel.addEventListener("change", () => onChange(sel.value || "clean"));
  } else if (spec.type === "texture") {
    el.innerHTML =
      `<header><label>${key}${hint}</label><output></output></header>` +
      `<select></select>`;
    /** @type {HTMLSelectElement} */
    const sel = pick(el, "select");
    sel.add(new Option("None", ""));
    for (const texture of state.boot.textures || []) {
      sel.add(new Option(texture.label, texture.path));
    }
    const current = value == null ? "" : String(value);
    if (current && ![...sel.options].some((option) => option.value === current)) {
      sel.add(new Option(`Custom: ${current}`, current));
    }
    sel.value = current;
    out.textContent = current || "null";
    sel.addEventListener("change", () => onChange(sel.value || null));
  } else if (spec.type === "path") {
    el.insertAdjacentHTML(
      "beforeend",
      `<div class="row"><input type="text" placeholder="null = off / system default"></div>`
    );
    /** @type {HTMLInputElement} */
    const input = pick(el, "input");
    input.value = String(value || "");
    pick(el, "label").innerHTML = `${key}${hint}`;
    input.addEventListener("input", () => onChange(input.value.trim() || null));
  } else if (spec.type === "text") {
    el.insertAdjacentHTML(
      "beforeend",
      `<div class="row"><input type="text" placeholder="null = off"></div>`
    );
    /** @type {HTMLInputElement} */
    const input = pick(el, "input");
    input.value = String(value || "");
    pick(el, "label").innerHTML = `${key}${hint}`;
    input.addEventListener("input", () => onChange(input.value || null));
  }
  return el;
}

function renderControls() {
  const wrap = $("#style-controls");
  wrap.innerHTML = "";
  for (const [key, spec] of Object.entries(state.boot.fields)) {
    wrap.appendChild(makeControl(key, spec, state.model[key]));
  }
}

function setStatusLine() {
  const meta = state.current;
  if (!meta) return;
  const line = `${meta.audio} · ${meta.image_count} image${meta.image_count === 1 ? "" : "s"}`;
  $("#track-meta").textContent = meta.artist ? `${line} · ${meta.artist}` : line;
}

/** @param {string} name */
function imageUrl(name) {
  return `/api/tracks/${encodeURIComponent(currentTrack().id)}/images/${encodeURIComponent(name)}`;
}

function renderImages() {
  const wrap = $("#image-grid");
  wrap.innerHTML = "";
  if (!state.current || !state.current.images) return;
  /** @type {string | null} */
  let dragName = null;
  if (!state.current.images.length) {
    const empty = document.createElement("span");
    empty.className = "dim image-empty";
    empty.textContent = "No images yet — use + Add.";
    wrap.appendChild(empty);
    return;
  }
  for (const name of state.current.images) {
    const card = document.createElement("div");
    card.className = "image-card";
    card.draggable = true;
    card.dataset.name = name;

    const img = document.createElement("img");
    img.src = imageUrl(name);
    img.alt = name;
    card.appendChild(img);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn small image-remove";
    remove.title = "Remove";
    remove.textContent = "×";
    remove.addEventListener("click", () => removeImage(name));
    card.appendChild(remove);

    const label = document.createElement("span");
    label.className = "image-name";
    label.textContent = name;
    card.appendChild(label);

    card.addEventListener("dragstart", (e) => {
      dragName = name;
      if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragover", (e) => e.preventDefault());
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      if (!dragName || dragName === name) return;
      const names = currentTrack().images.slice();
      const from = names.indexOf(dragName);
      const to = names.indexOf(name);
      names.splice(from, 1);
      // Insert before the card that was dropped on, compensating for the
      // removal shifting everything after `from` one slot left.
      names.splice(from < to ? to - 1 : to, 0, dragName);
      reorderImages(names);
    });
    wrap.appendChild(card);
  }
}

/** @param {string[]} names */
async function reorderImages(names) {
  setBusy(true, "saving order…");
  try {
    const track = currentTrack();
    /** @type {ImagesResult} */
    const res = await api(`/api/tracks/${encodeURIComponent(track.id)}/images`, {
      method: "PUT",
      body: JSON.stringify({ order: names }),
    });
    track.images = res.images;
    renderImages();
    schedulePreview();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

/** @param {string} name */
function removeImage(name) {
  if (!confirm(`Remove ${name}? The file will be deleted.`)) return;
  setBusy(true, "removing…");
  (async () => {
    try {
      const track = currentTrack();
      /** @type {ImagesResult} */
      const res = await api(`/api/tracks/${encodeURIComponent(track.id)}/images`, {
        method: "DELETE",
        body: JSON.stringify({ names: [name] }),
      });
      track.images = res.images;
      renderImages();
      schedulePreview();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  })();
}

/** @param {FileList | null} files */
async function addImages(files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("images", f);
  setBusy(true, "adding images…");
  try {
    const track = currentTrack();
    /** @type {ImagesResult} */
    const res = await api(`/api/tracks/${encodeURIComponent(track.id)}/images`, {
      method: "POST",
      body: fd,
    });
    track.images = res.images;
    renderImages();
    schedulePreview();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

async function deleteTrack() {
  const track = currentTrack();
  if (!confirm(`Delete track "${track.title}"? This removes the whole folder and cannot be undone.`)) {
    return;
  }
  setBusy(true, "deleting…");
  try {
    await api(`/api/tracks/${encodeURIComponent(track.id)}`, { method: "DELETE" });
    state.current = null;
    toast("track deleted");
    await loadTrackList();
    const first = state.tracks.find((t) => !t.error);
    if (first) {
      await selectTrack(first.id);
    } else {
      renderTrackList();
      $("#image-grid").innerHTML = "";
      $("#track").classList.add("hidden");
      $("#empty").classList.remove("hidden");
    }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

/** @param {string} id */
async function selectTrack(id) {
  setBusy(true, "loading…");
  try {
    /** @type {TrackDetail} */
    const detail = await api(`/api/tracks/${encodeURIComponent(id)}`);
    state.current = detail;
    state.model = { ...detail.style };
    renderControls();
    renderTrackList();
    renderImages();
    /** @type {HTMLInputElement} */ ($("#track-title")).value = detail.title || "";
    /** @type {HTMLInputElement} */ ($("#track-artist")).value = detail.artist || "";
    /** @type {HTMLInputElement} */ ($("#track-handle")).value = detail.handle || "";
    $("#track-meta").textContent = "";
    setStatusLine();
    $("#track").classList.remove("hidden");
    $("#empty").classList.add("hidden");
    await preview();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

function renderTrackList() {
  const ul = $("#track-list");
  ul.innerHTML = "";
  if (!state.tracks.length) {
    $("#empty").classList.remove("hidden");
    $("#track").classList.add("hidden");
    return;
  }
  $("#empty").classList.add("hidden");
  for (const t of state.tracks) {
    const li = document.createElement("li");
    if (t.error) {
      li.className = "error";
      li.textContent = `${t.title} — ${t.error}`;
    } else {
      const ok = /** @type {TrackSummary} */ (t);
      li.innerHTML =
        `<span>${ok.title}</span><span class="sub">${ok.preset} · ${ok.image_count} img</span>`;
      li.classList.toggle("active", state.current?.id === t.id);
      li.addEventListener("click", () => selectTrack(t.id));
    }
    ul.appendChild(li);
  }
}

async function loadTrackList() {
  state.tracks = await api("/api/tracks");
  renderTrackList();
}

/** @type {ReturnType<typeof setTimeout> | undefined} */
let debounceId;
function schedulePreview() {
  if (!state.current) return;
  clearTimeout(debounceId);
  debounceId = setTimeout(preview, 400);
}

async function preview() {
  if (!state.current) return;
  setBusy(true, "rendering…");
  try {
    const aspects = Object.keys(state.boot.aspects);
    /** @type {PreviewResult} */
    const res = await api(`/api/tracks/${encodeURIComponent(state.current.id)}/preview`, {
      method: "POST",
      body: JSON.stringify({ aspects, style: state.model }),
    });
    const stamp = Date.now();
    for (const [key, url] of Object.entries(res.urls || {})) {
      /** @type {HTMLVideoElement | null} */
      const video = document.querySelector(`#preview-grid video[data-aspect="${key}"]`);
      if (!video) continue;
      playVideo(video, `${url}?t=${stamp}`);
    }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

/** @type {ReturnType<typeof setTimeout> | undefined} */
let styleSaveId;
// Autosave style ~1s after the last change (issue #25). The server validates
// through resolve_style and only then writes, so invalid values never hit disk.
function scheduleStyleSave() {
  if (!state.current) return;
  clearTimeout(styleSaveId);
  styleSaveId = setTimeout(() => saveStyle(true), 1000);
}

/** @param {boolean} [silent] */
async function saveStyle(silent = false) {
  if (!state.current) return;
  if (!silent) setBusy(true, "saving…");
  try {
    /** @type {StyleSaveResult} */
    const res = await api(`/api/tracks/${encodeURIComponent(state.current.id)}/style`, {
      method: "POST",
      body: JSON.stringify({ style: state.model }),
    });
    state.model = res.resolved;
    if (silent) {
      $("#status").textContent = "autosaved";
    } else {
      renderControls();
      toast(`saved ${Object.keys(res.saved).length} diff key(s)`);
    }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    if (!silent) setBusy(false);
  }
}

async function resetStyle() {
  if (!state.current) return;
  const preset = state.model.preset || "clean";
  try {
    state.model = await api(`/api/style?preset=${encodeURIComponent(preset)}`);
    renderControls();
    schedulePreview();
    scheduleStyleSave();
    toast(`reset to ${preset}`);
  } catch (err) {
    toast(err.message, "error");
  }
}

/** @type {ReturnType<typeof setTimeout> | undefined} */
let metaSaveId;
function scheduleMetadataSave() {
  if (!state.current) return;
  clearTimeout(metaSaveId);
  metaSaveId = setTimeout(saveMetadata, 800);
}

async function saveMetadata() {
  if (!state.current) return;
  const field = (/** @type {string} */ sel) => /** @type {HTMLInputElement} */ ($(sel)).value;
  const title = field("#track-title").trim();
  if (!title) {
    toast("title cannot be empty", "error");
    return;
  }
  const body = {
    title,
    artist: field("#track-artist").trim(),
    handle: field("#track-handle").trim(),
  };
  try {
    /** @type {MetadataResult} */
    const res = await api(`/api/tracks/${encodeURIComponent(state.current.id)}/metadata`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
    const renamed = res.title !== state.current.title;
    state.current.title = res.title;
    state.current.artist = res.artist;
    state.current.handle = res.handle;
    await loadTrackList();
    setStatusLine();
    if (renamed) schedulePreview();
    $("#status").textContent = "metadata saved";
  } catch (err) {
    toast(err.message, "error");
  }
}

/**
 * @param {string} aspect
 * @param {Style} style
 * @param {boolean} [preview]
 * @returns {Promise<string | null>} the job id
 */
async function startJob(aspect, style, preview = false) {
  if (!state.current) return null;
  try {
    /** @type {JobCreated} */
    const res = await api(`/api/tracks/${encodeURIComponent(state.current.id)}/render`, {
      method: "POST",
      body: JSON.stringify({ aspect, style, preview }),
    });
    return res.id;
  } catch (err) {
    toast(err.message, "error");
    return null;
  }
}

async function pollJobs() {
  /** @type {JobSummary[]} */
  let jobs;
  try {
    jobs = await api("/api/jobs");
  } catch (_) {
    return;
  }
  const wrap = $("#jobs");
  if (!state.jobRows) state.jobRows = {};
  const seen = new Set();
  for (const j of jobs) {
    seen.add(j.id);
    let row = state.jobRows[j.id];
    if (!row) {
      const el = document.createElement("div");
      el.className = "job";
      el.innerHTML =
        `<div class="job-head"><span class="job-label"></span>` +
        `<div class="job-actions"><span class="job-status"></span>` +
        `<button type="button" class="btn small job-cancel">Cancel</button></div></div>` +
        `<div class="bar"><div class="fill"></div></div>` +
        `<div class="error-text hidden"></div>` +
        `<div class="job-video"><video controls loop muted playsinline></video></div>`;
      wrap.appendChild(el);
      /** @type {HTMLVideoElement} */
      const video = pick(el, "video");
      /** @type {HTMLButtonElement} */
      const cancel = pick(el, ".job-cancel");
      row = {
        label: pick(el, ".job-label"),
        status: pick(el, ".job-status"),
        fill: pick(el, ".fill"),
        err: pick(el, ".error-text"),
        cancel,
        cancelling: false,
        video,
        url: null,
        lastStatus: null,
      };
      cancel.addEventListener("click", () => cancelJob(j.id, row));
      state.jobRows[j.id] = row;
    }
    row.label.textContent = `${j.track} · ${j.aspect}${j.preview ? " (preview)" : ""}`;
    row.status.textContent = `${j.status} ${j.progress}%`;
    row.status.className = "job-status " + j.status;
    row.fill.style.width = `${j.progress}%`;
    row.fill.className = "fill " + (["skipped", "failed", "cancelled"].includes(j.status) ? j.status : "");
    const active = j.status === "pending" || j.status === "running";
    row.cancel.hidden = !active;
    if (!active) {
      row.cancelling = false;
      row.cancel.disabled = false;
      row.cancel.textContent = "Cancel";
    } else if (row.cancelling) {
      row.cancel.disabled = true;
      row.cancel.textContent = "cancelling…";
    } else {
      row.cancel.disabled = false;
      row.cancel.textContent = "Cancel";
    }
    if (j.error) {
      row.err.textContent = j.error;
      row.err.classList.remove("hidden");
    } else {
      row.err.classList.add("hidden");
    }
    const justFinished = j.status === "done" && row.lastStatus !== "done";
    if (j.url && (j.url !== row.url || justFinished)) {
      row.url = j.url;
      playVideo(row.video, `${j.url}?t=${Date.now()}`);
    }
    row.lastStatus = j.status;
  }
  for (const id of Object.keys(state.jobRows)) {
    if (!seen.has(id)) {
      /** @type {HTMLElement} */ (state.jobRows[id].status.closest(".job")).remove();
      delete state.jobRows[id];
    }
  }
}

/**
 * @param {string} id
 * @param {JobRow} row
 */
async function cancelJob(id, row) {
  if (row.cancelling) return;
  row.cancelling = true;
  row.cancel.disabled = true;
  row.cancel.textContent = "cancelling…";
  try {
    await api(`/api/jobs/${id}/cancel`, { method: "POST" });
    pollJobs();
  } catch (err) {
    row.cancelling = false;
    row.cancel.disabled = false;
    row.cancel.textContent = "Cancel";
    toast(err.message, "error");
  }
}

function startPolling() {
  pollJobs();
  setInterval(pollJobs, 700);
}

const AUDIO_EXTS = [".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".ogg", ".opus"];
const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"];

/** @param {string} name */
function ext(name) {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

/**
 * Split dropped files into the first audio file and all images.
 * @param {File[]} files
 */
function classifyDrop(files) {
  /** @type {File | null} */
  let audio = null;
  /** @type {File[]} */
  const images = [];
  for (const f of files) {
    const e = ext(f.name);
    if (AUDIO_EXTS.includes(e)) {
      if (!audio) audio = f;
    } else if (IMAGE_EXTS.includes(e)) {
      images.push(f);
    }
  }
  return { audio, images };
}

function renderDropPreview() {
  const wrap = $("#drop-preview");
  const picks = state.dropFiles;
  wrap.innerHTML = "";
  for (const f of picks) {
    const isAudio = AUDIO_EXTS.includes(ext(f.name));
    const chip = document.createElement("span");
    chip.className = "chip " + (isAudio ? "audio" : "");
    chip.textContent = f.name;
    const x = document.createElement("span");
    x.className = "x";
    x.textContent = "×";
    x.addEventListener("click", () => {
      state.dropFiles = state.dropFiles.filter((g) => g !== f);
      renderDropPreview();
    });
    chip.appendChild(x);
    wrap.appendChild(chip);
  }
  /** @type {HTMLButtonElement} */ ($("#create-track")).disabled = !state.dropFiles.some((f) => AUDIO_EXTS.includes(ext(f.name))) ||
    !state.dropFiles.some((f) => IMAGE_EXTS.includes(ext(f.name)));
}

function openModal() {
  state.dropFiles = [];
  renderDropPreview();
  $("#modal").classList.remove("hidden");
  $("#new-title").focus();
}
function closeModal() {
  $("#modal").classList.add("hidden");
}

async function createProject() {
  const { audio, images } = classifyDrop(state.dropFiles);
  if (!audio || images.length === 0) {
    toast("need one audio file and at least one image", "error");
    return;
  }
  const fd = new FormData();
  fd.append("title", /** @type {HTMLInputElement} */ ($("#new-title")).value.trim());
  fd.append("audio", audio);
  images.forEach((img) => fd.append("images", img));
  setBusy(true, "creating…");
  try {
    const res = await fetch("/api/tracks", { method: "POST", body: fd });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || res.statusText);
    }
    /** @type {CreatedTrack} */
    const track = await res.json();
    closeModal();
    await loadTrackList();
    await selectTrack(track.id);
    toast(`created ${track.title}`);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

function initDropzone() {
  const dz = $("#dropzone");
  /** @type {HTMLInputElement} */
  const input = $("#drop-input");
  dz.addEventListener("click", (e) => {
    if (e.target !== input) input.click();
  });
  input.addEventListener("change", () => {
    if (input.files) {
      state.dropFiles = state.dropFiles.concat([...input.files]);
      renderDropPreview();
    }
    input.value = "";
  });
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.add("over");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.remove("over");
    })
  );
  dz.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files.length) {
      state.dropFiles = state.dropFiles.concat([...e.dataTransfer.files]);
      renderDropPreview();
    }
  });
}

function buildPreviewGrid() {
  const grid = $("#preview-grid");
  grid.innerHTML = "";
  for (const [key, aspect] of Object.entries(state.boot.aspects)) {
    const cell = document.createElement("div");
    cell.className = "preview-cell";
    cell.innerHTML =
      `<div class="preview-cell-head"><span>${aspect.label}</span><span class="dim">${key}</span></div>` +
      `<video muted autoplay loop playsinline data-aspect="${key}"></video>`;
    grid.appendChild(cell);
  }
}

async function init() {
  /** @type {HTMLSelectElement} */
  const aspectSelect = $("#aspect");
  state.boot = await api("/api/boot");
  aspectSelect.innerHTML = Object.entries(state.boot.aspects)
    .map(([k, a]) => `<option value="${k}">${k} (${a.label})</option>`)
    .join("");
  aspectSelect.value = "square";
  buildPreviewGrid();
  $("#reset-style").addEventListener("click", resetStyle);
  ["#track-title", "#track-artist", "#track-handle"].forEach((sel) =>
    $(sel).addEventListener("input", scheduleMetadataSave)
  );
  $("#preview").addEventListener("click", preview);
  /** @type {NodeListOf<HTMLButtonElement>} */
  const renderButtons = document.querySelectorAll("[data-render]");
  renderButtons.forEach((btn) => {
    btn.addEventListener("click", () =>
      startJob(/** @type {string} */ (btn.dataset.render), state.model)
    );
  });
  $("#cmp-a").addEventListener("click", () => {
    state.cmpA = { ...state.model };
    toast("A set");
  });
  $("#cmp-b").addEventListener("click", () => {
    state.cmpB = { ...state.model };
    toast("B set");
  });
  $("#cmp-run").addEventListener("click", async () => {
    if (!state.cmpA || !state.cmpB) {
      toast("Set A and B first", "error");
      return;
    }
    const aspect = aspectSelect.value;
    // Short preview clips, content-addressed per look (#41): fast, and never
    // overwrite the real exports in out/<id>/.
    const idA = await startJob(aspect, state.cmpA, true);
    const idB = await startJob(aspect, state.cmpB, true);
    if (!idA || !idB) return;
    state.cmpIds = [idA, idB];
    toast("rendering A/B…");
    waitForCompare();
  });
  $("#new-project").addEventListener("click", openModal);
  $("#modal-close").addEventListener("click", closeModal);
  $("#create-track").addEventListener("click", createProject);
  /** @type {HTMLInputElement} */
  const addImagesInput = $("#add-images-input");
  $("#add-images").addEventListener("click", () => addImagesInput.click());
  addImagesInput.addEventListener("change", () => {
    addImages(addImagesInput.files);
    addImagesInput.value = "";
  });
  $("#delete-track").addEventListener("click", deleteTrack);
  state.dropFiles = [];
  initDropzone();
  startPolling();
  await loadTrackList();
  const first = state.tracks.find((t) => !t.error);
  if (first) await selectTrack(first.id);
}

function waitForCompare() {
  clearInterval(state.cmpTimer);
  const ids = /** @type {string[]} */ (state.cmpIds);
  const ud = setInterval(async () => {
    /** @type {JobSummary[]} */
    let jobs;
    try {
      jobs = await api("/api/jobs");
    } catch (err) {
      clearInterval(ud);
      toast(err.message, "error");
      return;
    }
    /** @type {Record<string, string>} */
    const urls = {};
    for (const j of jobs) {
      if (!ids.includes(j.id)) continue;
      if (j.status === "failed" || j.status === "cancelled") {
        clearInterval(ud);
        toast(`A/B ${j.status}${j.error ? `: ${j.error}` : ""}`, "error");
        return;
      }
      if (j.url) urls[j.id] = j.url;
    }
    if (urls[ids[0]] && urls[ids[1]]) {
      clearInterval(ud);
      $("#compare").classList.remove("hidden");
      playVideo($("#cmp-a-video"), urls[ids[0]] + `?t=${Date.now()}`);
      playVideo($("#cmp-b-video"), urls[ids[1]] + `?t=${Date.now()}`);
    }
  }, 700);
  state.cmpTimer = ud;
}

init().catch((err) => toast(err.message, "error"));