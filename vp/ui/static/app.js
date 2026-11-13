"use strict";

const state = { boot: null, tracks: [], current: null, model: {} };
const $ = (sel) => document.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (_) { /* not json */ }
    throw new Error(detail);
  }
  return res.json();
}

function toast(msg, kind = "ok") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast ${kind}`;
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 2200);
}

function setBusy(busy, msg = "") {
  $("#status").textContent = msg;
  $("#status").classList.toggle("busy", busy);
}

function makeControl(key, spec, value) {
  const el = document.createElement("div");
  el.className = "control";
  const hint = spec.help ? `<span class="hint">${spec.help}</span>` : "";
  el.innerHTML = `<header><label>${key}${hint}</label><output></output></header>`;
  const out = el.querySelector("output");
  const onChange = (v) => {
    state.model[key] = v;
    out.textContent = v === null ? "null" : String(v);
    schedulePreview();
  };

  if (spec.type === "bool") {
    el.innerHTML = `<div class="row"><label>${key}${hint}</label><input type="checkbox"></div>`;
    const cb = el.querySelector("input");
    cb.checked = !!value;
    cb.addEventListener("change", () => onChange(cb.checked));
  } else if (spec.type === "int" || spec.type === "float") {
    const min = spec.min ?? 0;
    const max = spec.max ?? 1;
    const step = spec.step ?? (spec.type === "int" ? 1 : 0.01);
    el.insertAdjacentHTML(
      "beforeend",
      `<div class="slider-group"><input type="range" min="${min}" max="${max}" step="${step}"><output></output></div>`
    );
    const slider = el.querySelector("input[type=range]");
    slider.value = value ?? 0;
    out.textContent = String(value);
    slider.addEventListener("input", () => onChange(parseFloat(slider.value)));
  } else if (spec.type === "choice") {
    const numeric = spec.choices.length && typeof spec.choices[0] === "number";
    el.innerHTML =
      `<header><label>${key}${hint}</label><output></output></header>` +
      `<select>${spec.choices.map((c) => `<option value="${c}">${c}</option>`).join("")}</select>`;
    const sel = el.querySelector("select");
    sel.value = String(value);
    out.textContent = String(value);
    sel.addEventListener("change", () =>
      onChange(numeric ? Number(sel.value) : sel.value)
    );
  } else if (spec.type === "path") {
    el.insertAdjacentHTML(
      "beforeend",
      `<div class="row"><input type="text" placeholder="null = off / system default"></div>`
    );
    const input = el.querySelector("input");
    input.value = value || "";
    el.querySelector("label").innerHTML = `${key}${hint}`;
    input.addEventListener("input", () => onChange(input.value.trim() || null));
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

function setStatusLine(key) {
  const meta = state.current;
  if (!meta) return;
  const line = `${meta.audio} · ${meta.image_count} image${meta.image_count === 1 ? "" : "s"}`;
  $("#track-meta").textContent = meta.artist ? `${line} · ${meta.artist}` : line;
}

async function selectTrack(slug) {
  setBusy(true, "loading…");
  try {
    const detail = await api(`/api/tracks/${slug}`);
    state.current = detail;
    state.model = { ...detail.style };
    renderControls();
    renderTrackList();
    $("#track-title").textContent = detail.title;
    $("#track-meta").textContent = "";
    setStatusLine();
    $("#track").classList.remove("hidden");
    $("#empty").classList.add("hidden");
    $("#save").disabled = false;
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
      li.innerHTML =
        `<span>${t.title}</span><span class="sub">${t.preset} · ${t.image_count} img</span>`;
      li.classList.toggle("active", state.current && state.current.slug === t.slug);
      li.addEventListener("click", () => selectTrack(t.slug));
    }
    ul.appendChild(li);
  }
}

async function loadTrackList() {
  state.tracks = await api("/api/tracks");
  renderTrackList();
}

let debounceId = null;
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
    const res = await api(`/api/tracks/${state.current.slug}/preview`, {
      method: "POST",
      body: JSON.stringify({ aspects, style: state.model }),
    });
    const stamp = Date.now();
    for (const [key, url] of Object.entries(res.urls || {})) {
      const video = document.querySelector(
        `#preview-grid video[data-aspect="${key}"]`
      );
      if (!video) continue;
      video.src = `${url}?t=${stamp}`;
      video.load();
    }
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

async function saveStyle() {
  if (!state.current) return;
  setBusy(true, "saving…");
  try {
    const res = await api(`/api/tracks/${state.current.slug}/style`, {
      method: "POST",
      body: JSON.stringify({ style: state.model }),
    });
    state.model = res.resolved;
    renderControls();
    toast(`saved ${Object.keys(res.saved).length} diff key(s)`);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

async function startJob(aspect, style, preview = false) {
  if (!state.current) return null;
  try {
    const res = await api(`/api/tracks/${state.current.slug}/render`, {
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
        `<div class="job-head"><span class="job-label"></span><span class="job-status"></span></div>` +
        `<div class="bar"><div class="fill"></div></div>` +
        `<div class="error-text hidden"></div>` +
        `<div class="job-video"><video controls loop muted playsinline></video></div>`;
      wrap.appendChild(el);
      const video = el.querySelector("video");
      row = {
        label: el.querySelector(".job-label"),
        status: el.querySelector(".job-status"),
        fill: el.querySelector(".fill"),
        err: el.querySelector(".error-text"),
        video,
        url: null,
      };
      state.jobRows[j.id] = row;
    }
    row.label.textContent = `${j.slug} · ${j.aspect}${j.preview ? " (preview)" : ""}`;
    row.status.textContent = `${j.status} ${j.progress}%`;
    row.status.className = "job-status " + j.status;
    row.fill.style.width = `${j.progress}%`;
    row.fill.className = "fill " + (["skipped", "failed", "cancelled"].includes(j.status) ? j.status : "");
    if (j.error) {
      row.err.textContent = j.error;
      row.err.classList.remove("hidden");
    } else {
      row.err.classList.add("hidden");
    }
    if (j.url && j.url !== row.url) {
      row.url = j.url;
      row.video.src = j.url;
      row.video.load();
      row.video.play().catch(() => {});
    }
  }
  for (const id of Object.keys(state.jobRows)) {
    if (!seen.has(id)) {
      state.jobRows[id].status.closest(".job").remove();
      delete state.jobRows[id];
    }
  }
}

function startPolling() {
  pollJobs();
  setInterval(pollJobs, 700);
}

const AUDIO_EXTS = [".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".ogg", ".opus"];
const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"];

function ext(name) {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

function classifyDrop(files) {
  let audio = null;
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
  $("#create-track").disabled = !state.dropFiles.some((f) => AUDIO_EXTS.includes(ext(f.name))) ||
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
  fd.append("title", $("#new-title").value.trim());
  fd.append("audio", audio);
  images.forEach((img) => fd.append("images", img));
  setBusy(true, "creating…");
  try {
    const res = await fetch("/api/tracks", { method: "POST", body: fd });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || res.statusText);
    }
    const track = await res.json();
    closeModal();
    await loadTrackList();
    await selectTrack(track.slug);
    toast(`created ${track.title}`);
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(false);
  }
}

function initDropzone() {
  const dz = $("#dropzone");
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
  state.boot = await api("/api/boot");
  $("#aspect").innerHTML = Object.entries(state.boot.aspects)
    .map(([k, a]) => `<option value="${k}">${k} (${a.label})</option>`)
    .join("");
  $("#aspect").value = "square";
  buildPreviewGrid();
  $("#save").addEventListener("click", saveStyle);
  $("#preview").addEventListener("click", preview);
  document.querySelectorAll("[data-render]").forEach((btn) => {
    btn.addEventListener("click", () => startJob(btn.dataset.render, state.model));
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
    const aspect = $("#aspect").value;
    const idA = await startJob(aspect, state.cmpA);
    const idB = await startJob(aspect, state.cmpB);
    state.cmpIds = [idA, idB];
    toast("rendering A/B…");
    waitForCompare();
  });
  $("#new-project").addEventListener("click", openModal);
  $("#modal-close").addEventListener("click", closeModal);
  $("#create-track").addEventListener("click", createProject);
  state.dropFiles = [];
  initDropzone();
  startPolling();
  await loadTrackList();
  const first = state.tracks.find((t) => !t.error);
  if (first) await selectTrack(first.slug);
}

function waitForCompare() {
  const ud = setInterval(async () => {
    const jobs = await api("/api/jobs");
    const urls = {};
    for (const j of jobs) {
      if (state.cmpIds.includes(j.id) && j.url) urls[j.id] = j.url;
    }
    if (urls[state.cmpIds[0]] && urls[state.cmpIds[1]]) {
      clearInterval(ud);
      $("#compare").classList.remove("hidden");
      $("#cmp-a-video").src = urls[state.cmpIds[0]] + `?t=${Date.now()}`;
      $("#cmp-b-video").src = urls[state.cmpIds[1]] + `?t=${Date.now()}`;
      $("#cmp-a-video").load();
      $("#cmp-b-video").load();
    }
  }, 700);
}

init().catch((err) => toast(err.message, "error"));