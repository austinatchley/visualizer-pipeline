# Visualizer Pipeline — Design

Status: **v1 + post-v1 web studio shipped** (decisions in §11; open work in §12)

## 1. Problem

I have a catalog of beats plus a small set of stills (performance photos, mood-board
images). I want to turn each beat into a short-form visualizer for Reels, feed, and
YouTube without editing every track by hand. The pipeline must be:

- **CLI-first** and **batchable** — drop a folder, run one command, get exports.
- **Deterministic** — same inputs and same settings produce the same video.
- **Cheap per track** — a new beat needs a folder and 3 images, nothing else.
- **Aspect-native** — one edit, rendered as 16:9 full and 9:16 / 1:1 short hooks.
- **Musical** — cuts and motion are keyed to the beat and to the track's energy.
- **Vibe-aware** — the look can follow the music (presets per track now, auto later).

Non-goal (v1): a timeline editor, real-time/live visuals, generated imagery.

## 2. Success criteria

- `vp batch tracks/` renders every track folder to all variants with no per-track edits.
- A 3-minute beat renders in ≤3 min per variant on an M-series Mac (16:9 full is the
  slowest; hooks are cheaper).
- Renders are beat-accurate: cuts land on the intended beats within one frame.
- Hook exports are 30–60 s and start on a musically sensible boundary.
- Re-running skips up-to-date outputs and is safe to interrupt.
- A failed track does not abort the batch; the summary names it.

## 3. Input contract

One folder per track:

```
tracks/midnight-drive/
  audio.wav            # or mp3/flac/m4a/aiff/ogg; exactly one
  title.txt            # OR track.json -> "title"
  images/              # OR images in the folder root
    01.jpg
    02.jpg
    03.jpg
  clips/               # reserved for v2 video snippets
  track.json           # optional manifest (overrides + metadata)
```

Rules:

- Audio: first matching file in root; more than one requires `audio` in the manifest.
- Images: `images/` if present, else image files in root; sorted by filename; 1+ required
  (3 is the expected default; fewer simply repeat).
- Title: `track.json.title` → `title.txt` → cleaned folder name.
- Image originals are never modified. Cropping/scaling happens at render time so the
  same source works for every aspect.

### Manifest (`track.json`, optional)

```json
{
  "title": "Midnight Drive",
  "artist": "AUSTIN",
  "handle": "@austinatchley",
  "audio": "audio.wav",
  "images": ["images/01.jpg", "images/02.jpg", "images/03.jpg"],
  "style": {
    "preset": "clean",
    "beats_per_cut": 8,
    "crossfade": 0.35,
    "zoom": 0.10,
    "fps": 30,
    "waveform": false,
    "title_font": null
  },
  "hook": {
    "mode": "auto",
    "target": 45.0,
    "start": null,
    "end": null
  },
  "overrides": {
    "bpm": 140,
    "tempo_multiplier": 1,
    "cuts": [4.2, 12.8, 21.4],
    "hook": { "start": 32.0, "end": 76.0 }
  }
}
```

Precedence (low → high): built-in defaults → preset → `track.json.style` → CLI flags.
Only put values in the manifest that differ from the defaults.

## 4. Pipeline

```
discover → analyze (cached) → plan timeline(s) → build HUD → render variants → report
```

Each stage is a separate module so it can be unit-tested and reused:

| Stage | Module | Output |
|---|---|---|
| Discover inputs | `track.py` | `Track` object |
| Analyze audio | `analyze.py` | `.vp/beatmap.json` |
| Plan cuts/timeline | `plan.py` (pure) | `Plan` (boundaries, segments, offsets, hook window) |
| Build overlays | `hud.py` | `hud.png` per aspect |
| Render | `render.py` | `.mp4` per variant |
| Orchestrate | `cli.py` | exit code + summary |

Planning produces two windows from one analysis:

- **full** — the whole track, used by the 16:9 export.
- **hook** — a 30–60 s high-energy excerpt, used by 9:16 and 1:1.

Both use the same energy-reactive planner and crossfade math; only the window and the
HUD/composition differ. This keeps the hook versions musically consistent with the full
edit.

### 4.1 Analysis

Use `librosa` at 22050 Hz mono. Extract:

- `duration`, `tempo` (BPM), `beats` (seconds), `downbeats` (every 4th beat)
- per-beat `energy` (RMS interpolated at beat times, normalized 0–1)
- bar (4-beat) and phrase (16-beat) energy aggregates, normalized
- onset novelty per beat (for section/drop detection)

Cache to `<track>/.vp/beatmap.json`. Invalidate when the audio file's size/mtime or the
`analysis_version` changes. `--reanalyze` forces a rebuild.

**Tempo ambiguity:** beat trackers routinely detect half/double time (the `c_mixed`
fixture at 140 BPM reads 92.3). Provide `overrides.bpm`, `overrides.tempo_multiplier`
(0.5 / 1 / 2), and `--bpm` so a track can be corrected without re-analyzing.

```json
// beatmap.json
{
  "analysis_version": 2,
  "audio": "...",
  "sr": 22050,
  "duration": 90.0,
  "tempo": 117.5,
  "beats": [0.47, 0.98, ...],
  "downbeats": [0.47, 2.53, ...],
  "beat_energy": [0.12, 0.88, ...],
  "bar_energy": [0.31, 0.74, ...],
  "phrase_energy": [0.28, 0.81, ...],
  "onset_novelty": [0.05, 0.62, ...]
}
```

### 4.2 Timeline planning (energy-reactive)

Boundaries tile a window: `[0, cut_1, cut_2, ..., window_end]`. Cuts are chosen from
musical events, not a fixed interval:

1. **Section boundaries** — detect where phrase energy changes by ≥ threshold (default
   0.2) or onset novelty spikes; snap to the nearest downbeat. These are guaranteed cut
   candidates.
2. **Adaptive interval inside a section** — `beats_per_cut` (default 8) is the *base*
   interval, scaled by the local bar energy:
   - high energy (≥0.70) → `max(2, base/2)` beats (cut faster)
   - mid energy → `base` beats
   - low energy (≤0.35) → `base*2` beats (hold the shot)
3. **Snap + guard** — snap to downbeats when close, skip guard bands (first ~1.2 s,
   last ~1.5 s), enforce a minimum gap (default 1.0 s / 2 beats), dedupe.
4. **Overrides** — `overrides.cuts` replaces the automatic list entirely.

Fallback: if analysis fails or energy is flat, use the fixed `beats_per_cut` interval.
Each boundary records a reason (`section`, `drop`, `energy`, `interval`) so `vp inspect`
can explain the edit.

Crossfade timing (validated in the prototype) — with fade length `x` and boundary `b_i`:

- transition `i` starts at `offset_i = b_i - x` (clip `i` is fully visible by `b_i`)
- clip duration `d_i = (b_{i+1} - b_i) + x + slack` for all but the last
- last clip duration `= window_end - offset_last`
- enforce `x ≤ 0.4 × shortest_segment` and `slack = 0.1 s`

This preserves audio sync: the total output equals the window length and every transition
is centered on its beat.

### 4.3 Hook selection (9:16 and 1:1)

Select a short window from the full track:

- Target 45 s, clamped to 30–60 s and to the track length.
- Score candidate start points on downbeats / section boundaries:
  `mean(bar_energy) + 0.5*mean(onset_novelty) + bonus for starting at an energy rise`,
  minus a penalty for starting mid-phrase.
- Prefer a window that begins at a drop/section increase and contains the highest-energy
  plateau; require the window end to land on a boundary.
- Rebase the window to 0, then run the timeline planner inside it. Audio is trimmed to
  the same window.
- `hook.mode: "full"` disables the hook (render the whole track vertically/square).
- `overrides.hook` or `--hook START-END` forces an exact window.

### 4.4 Visual system

Layers, bottom to top:

1. **Base image** — cover-crop to the aspect, supersampled `style.supersample` (2×),
   Ken Burns zoom (`1.0 ↔ 1+zoom`), alternating direction each segment.
2. **Grade / effects** — `contrast`, optional `vignette`, fixed-seed `grain`, and an
   optional `texture` image blended with `texture_blend` (`multiply|overlay|screen|softlight`).
3. **Waveform strip** — optional scrolling Pillow strip (`vp/waveform.py`), tied to the
   beat energy; never uses ffmpeg's `showwaves`.
4. **HUD** — bottom gradient, title, and a smaller meta line (`artist · handle`).
   Pre-rendered as a transparent PNG with Pillow (Homebrew ffmpeg has no `drawtext`),
   faded in with alpha.
5. **Global fade** — 0.4 s in, 0.8 s out (video + audio).

Moves are intentionally few and reused: Ken Burns + beat/energy-aligned crossfade cuts +
`beat_punch` + title fade. This is what makes it look intentional instead of random.

**Presets and vibe.** `clean`, `punchy`, and `balanced` ship in `presets/*.json`, and
`style.preset` lets each track pick a look. Style keys are declared once in
`config.STYLE_FIELDS`; validation and the UI controls both read it, so new keys appear
everywhere automatically. `style.preset: "auto"` (vibe-aware mapping from audio features)
is still open (#19); explicit presets always win over `auto`.

Per-aspect layout:

| | 9:16 (1080×1920) | 1:1 (1080×1080) | 16:9 (1920×1080) |
|---|---|---|---|
| Title size | 8.5% of width | 9% of width | 7.5% of height |
| Title position | centered, block bottom at 80% height | centered, block bottom at 82% | left, block bottom at 87% |
| Max text width | 86% | 82% | 55% |
| Meta line | centered below title | centered below title | left below title |
| Safe area | bottom ~15% / right ~12% | bottom ~10% | standard margins |

Text wraps to width using font metrics. Default font: `Arial Bold.ttf`; configurable via
`style.title_font`. Handle is optional and rendered smaller/muted after the artist.

### 4.5 Render

Build one `ffmpeg -filter_complex` graph per variant:

```
[image per segment] → scale (supersample×) cover crop → zoompan (Ken Burns + beat_punch)
→ settb/setpts → xfade chain (beat offsets) → grade (contrast/vignette/grain/texture)
→ [optional Pillow waveform strip] → HUD PNG overlay (alpha fade-in)
→ global fades → scale=out_range=tv,format=yuv420p
audio: atrim(window) → loudnorm -14 LUFS → aresample 48k → afade
encode: libx264 crf 18, preset medium, AAC 192k, +faststart, yuv420p, 30 fps
```

Variants: `16x9` full, `9x16` hook, `1x1` hook. Hooks trim both video and audio to the
selected window.

Renderer notes:

- HUD is a Pillow PNG because ffmpeg lacks `drawtext`.
- Force `aresample=48000` after `loudnorm` (prototype emitted 96 kHz).
- Supersample factor is configurable (`style.supersample`, default 2×).
- `preview=True` renders a short, quarter-resolution, audio-less clip through the same
  graph for fast UI iteration.
- `progress=` / `cancel=` stream ffmpeg progress and abort mid-render; the UI runs full
  renders as background jobs.
- The waveform is a Pillow strip (`vp/waveform.py`) composited on top; the crashing
  `showwaves` path is gone.
- Temp files go in a per-render temp dir, always cleaned up.

## 5. CLI

```
vp analyze <track> [--bpm N] [--tempo-multiplier 0.5|1|2]
vp inspect <track>                     # print plan: cuts, windows, style, outputs
vp render  <track> [options]           # render one track
vp batch   <root>  [options]           # render every track folder under root
vp init    <track>                     # scaffold track.json / images/
vp ui      [--host H] [--port P] [--tracks DIR] [--out DIR] [--no-browser]
```

Common flags: `--preset`, `--beats-per-cut`, `--crossfade`, `--zoom`, `--fps`,
`--supersample`, `--contrast`, `--beat-punch`, `--vignette`, `--grain`, `--texture`,
`--texture-blend`, `--font`, `--handle`, `--bpm`, `--tempo-multiplier`,
`--waveform/--no-waveform`.

Behavior:

- `--aspect`: `vertical`, `horizontal`, `square`, or `all` (default: all).
- `--hook`: `auto` (default), `full` (no trimming), or `START-END` seconds.
- `--force`: re-render even if outputs are newer than inputs.
- `--jobs`: tracks rendered concurrently (default 1; ffmpeg is already multithreaded).
- Batch isolates failures, prints a `rendered n/m` summary, exits non-zero if any failed.
- Idempotency: skip when output mtime > newest input/beatmap mtime unless `--force`.

### 5.1 Local web UI (post-v1)

The CLI is the primary interface and the source of truth. `vp ui` (`vp/ui/server.py`)
is a thin FastAPI wrapper over the same config/plan/render functions, keeping previews
and exports byte-identical to a headless run. Key constraints:

- **One style schema.** `config.STYLE_FIELDS` declaratively describes every style key
  (type, min/max/step, choices, help). `_validate_style` and the UI's generated controls
  both consume it, so adding a key later makes it validate *and* appear in the UI.
- **Deterministic preview.** `render(..., preview=True)` renders a short, quarter-
  resolution, audio-less clip for fast iteration. The UI shows all three aspects side by
  side (9:16, 1:1, 16:9) and slices around a real cut so the preview shows a transition.
  It is the same renderer, only smaller.
- **Jobs + progress.** `render(..., progress=cb, cancel=cb)` streams ffmpeg
  `-progress` (percent 0–100) and can be aborted mid-render. The UI runs full renders
  as background jobs with the same idempotency as the CLI (shared `vp/status.py`).
- **Minimal save.** The UI persists only the diff between the edited style and the
  resolved preset baseline, so `track.json` stays minimal and preset changes propagate.
- **Create projects.** `POST /api/tracks` (multipart: one audio + images + title)
  scaffolds a valid track folder reusing the DESIGN §3 contract and `track.py`
  validation, so anything created in the UI is exactly what the CLI accepts.
- Optional dependency: `pip install -e ".[ui]"`. Base install is unchanged.

## 6. Outputs

```
out/<track-slug>/
  <track-slug>_16x9.mp4        # full track
  <track-slug>_9x16_hook.mp4   # 30–60s excerpt
  <track-slug>_1x1_hook.mp4    # 30–60s excerpt
```

Slug = lowercased title with non-alphanumerics collapsed to `-`. Override the root with
`--out`. `--debug` copies the beatmap and chosen hook window next to the outputs.

## 7. Repo layout (target)

```
visualizer-pipeline/
  pyproject.toml
  README.md
  DESIGN.md
  AGENTS.md
  vp/
    cli.py  track.py  analyze.py  plan.py  hud.py  waveform.py
    render.py  config.py  status.py
    ui/                       # local web studio
      server.py  static/
  presets/
    clean.json  punchy.json  balanced.json
  tests/
    test_config.py  test_plan.py  test_analyze.py  test_windows.py
    test_hud.py  test_waveform.py  test_cli.py  test_render_smoke.py  test_ui.py
  scripts/
    make_fixtures.py
  fixtures/                           # generated, gitignored
  out/                                # generated, gitignored
```

## 8. Testing

- **Unit:** timeline math (offsets, durations, total = window, no negative offsets),
  energy-reactive cut selection, hook scoring/rebasing, tempo overrides, text
  wrapping/layout, discovery/precedence, slug/naming, cache invalidation, config schema.
- **Integration:** render `fixtures/d_short` for all variants; assert with `ffprobe`:
  resolution (1080×1920, 1080×1080, 1920×1080), fps, duration ±0.1 s, 48 kHz audio,
  faststart, TV-range yuv420p; hook window alignment on a long fixture.
- **Waveform + UI:** Pillow strip output and FastAPI endpoints/jobs (skipped cleanly when
  the `.[dev,ui]` extras are absent).
- **CI:** `.github/workflows/ci.yml` installs `.[dev,ui]`, installs ffmpeg + DejaVu fonts,
  generates fixtures, and runs `pytest -q` (86 tests at the time of writing).

## 9. Performance

- Analysis is a one-time cost per track (cached; ~1–3 s).
- Rendering is dominated by `zoompan` + x264. Prototype: ~0.7× realtime for 9:16 at
  2× supersample. Target ≤ 1× realtime for the full 16:9 render.
- Hook exports (9:16 / 1:1) are cheaper because they only render 30–60 s.
- Optimizations kept in reserve: lower supersample factor, `preset veryfast`,
  pre-scaled image cache, concurrent variants.

## 10. Prototype issues (resolved)

- `showwaves` SIGKILL → replaced with the Pillow waveform strip (#15).
- `loudnorm` → 96 kHz → forced `aresample=48000`.
- No `drawtext` in Homebrew ffmpeg → Pillow HUD.
- Synthetic fixtures mis-detect tempo → documented `overrides.bpm` / `tempo_multiplier`.
- Horizontal output → verified by the ffprobe smoke tests.
- CI red after the UI landed → install `.[dev,ui]` + `httpx` (#26).

## 11. Decisions (locked, status updated post-v1)

- **D1 — Visual preset.** Shipped: `clean`, `punchy`, and `balanced` presets with
  per-track `style.preset`. Vibe-aware `auto` selection is still open (#19).
- **D2 — Cut strategy.** Energy-reactive: hold through low-energy sections, cut faster on
  high-energy/drops. Fixed interval is the fallback and `overrides.cuts` is the escape
  hatch.
- **D3 — Audio strip.** Resolved: deterministic scrolling Pillow strip (#15); ffmpeg
  `showwaves` is never used.
- **D4 — Video snippets.** Still open: images only; `clips/` segments tracked in #14.
- **D5 — Text.** Title + artist + handle, preserve case, bold sans, shadow.
- **D6 — Naming.** `<slug>_16x9.mp4`, `<slug>_9x16_hook.mp4`, `<slug>_1x1_hook.mp4`.
- **D7 — Tempo override.** `overrides.bpm` + `tempo_multiplier` + `--bpm`.
- **D8 — Batch concurrency.** Default `--jobs 1`; expose the flag.
- **D9 — Loudness.** -14 LUFS integrated, -1.5 dBTP true peak, 48 kHz AAC.
- **D10 — FPS.** 30 default; 60 optional later.
- **D11 — Length/versioning.** 16:9 is the full track; 9:16 and 1:1 are 30–60 s hooks
  (default 45 s), auto-selected or manually overridden.
- **D12 — Formats.** 9:16, 1:1, and 16:9 at 30 fps.
- **D13 — Vibe tailoring.** Per-track presets shipped; `style.preset: "auto"` is open
  (#19) and explicit presets always win.
- **D14 — Color/effects.** `contrast`, `beat_punch`, `vignette`, `grain`, `texture`, and
  `texture_blend` shipped via `config.STYLE_FIELDS`. Saturation/grade and beat-reactive
  color swings are backlog (#20/#21).
- **D15 — Web studio.** `vp ui` is an optional FastAPI client over the same engine
  (preview/jobs/create-project); the CLI remains the source of truth. Issues #23–#25
  track the remaining UI work.

## 12. Milestones

- **M0 — Prototype (done):** discovery, librosa analysis, ffmpeg Ken Burns render, CLI,
  fixtures.
- **M1 — Design locked (done).**
- **M2 — Core rebuild (done):** config/presets + `STYLE_FIELDS`, energy-reactive planner +
  hook windows, HUD (title/artist/handle), renderer (3 variants, 48 kHz, TV-range),
  CLI (aspects, hook, idempotency, jobs, inspect), unit + ffprobe tests, CI (with the
  `.[dev,ui]` follow-up in #26).
- **M3 — Polished (mostly shipped):** `clean`/`punchy`/`balanced` presets (#10), Pillow
  waveform (#15), grain/vignette/texture (#16), README (#13). Open: advanced
  section/phrase detection (#11), performance (#12), vibe-aware presets (#19).
- **M4 — Expansion:** video snippets (#14) still open; waveform and overlays landed early.
- **Post-v1 — Web studio:** foundation, jobs/progress/playback/A-B, and create-project are
  implemented; remaining UI work is tracked in #23–#25. Color backlog: #20/#21.