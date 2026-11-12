# Visualizer Pipeline — Design

Status: **draft for review** (prototype exists; this document is the contract for the rebuild)

## 1. Problem

I have a catalog of beats plus a small set of stills (performance photos, mood-board
images). I want to turn each beat into a short-form visualizer for Reels and YouTube
without editing every track by hand. The pipeline must be:

- **CLI-first** and **batchable** — drop a folder, run one command, get exports.
- **Deterministic** — same inputs and same settings produce the same video.
- **Cheap per track** — a new beat needs a folder and 3 images, nothing else.
- **Aspect-native** — one edit, rendered as both 9:16 and 16:9.
- **Musical** — cuts and motion are keyed to the beat, not random.

Non-goal (v1): a timeline editor, real-time/live visuals, generated imagery.

## 2. Success criteria

- `vp batch tracks/` renders every track folder to both aspects with no per-track edits.
- A 3-minute beat renders in under ~2 minutes per aspect on an M-series Mac.
- Renders are beat-accurate: cuts land on the intended beats within one frame.
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
  track.json           # optional manifest (overrides + metadata)
```

Rules:

- Audio: first matching file in root; more than one requires `audio` in the manifest.
- Images: `images/` if present, else image files in root; sorted by filename; 1+ required
  (3 is the expected default; fewer simply repeat).
- Title: `track.json.title` → `title.txt` → cleaned folder name.
- Image originals are never modified. Cropping/scaling happens at render time so the
  same source works for both aspects.

### Manifest (`track.json`, optional)

```json
{
  "title": "Midnight Drive",
  "artist": "AUSTIN",
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
  "overrides": {
    "bpm": 140,
    "cuts": [4.2, 12.8, 21.4],
    "sections": [{ "start": 0.0, "label": "intro" }]
  }
}
```

Precedence (low → high): built-in defaults → preset → `track.json.style` → CLI flags.
Only put values in the manifest that differ from the defaults.

## 4. Pipeline

```
discover → analyze (cached) → plan timeline → build HUD → render aspects → report
```

Each stage is a separate module so it can be unit-tested and reused:

| Stage | Module | Output |
|---|---|---|
| Discover inputs | `track.py` | `Track` object |
| Analyze audio | `analyze.py` | `.vp/beatmap.json` |
| Plan cuts/timeline | `plan.py` (pure) | `Plan` (boundaries, segments, offsets) |
| Build overlays | `hud.py` | `hud.png` per aspect |
| Render | `render.py` | `.mp4` per aspect |
| Orchestrate | `cli.py` | exit code + summary |

The **plan is shared by both aspects**. Both exports use the same cut times and the
same image order; only layout/composition changes. This keeps the vertical and
horizontal versions feeling like the same edit.

### 4.1 Analysis

Use `librosa` at 22050 Hz mono. Extract:

- `duration`, `tempo` (BPM), `beats` (seconds), `downbeats` (every 4th beat)
- per-beat `energy` (RMS interpolated at beat times, normalized 0–1)
- (v2) onset novelty / section boundaries

Cache to `<track>/.vp/beatmap.json`. Invalidate when the audio file's size/mtime or the
`analysis_version` changes. `--reanalyze` forces a rebuild.

**Tempo ambiguity:** beat trackers routinely detect half/double time (the `c_mixed`
fixture at 140 BPM reads 92.3). Provide `overrides.bpm` and a `tempo_multiplier`
(0.5 / 1 / 2) so a track can be corrected without re-analyzing.

```json
// beatmap.json
{
  "analysis_version": 1,
  "audio": "...",
  "sr": 22050,
  "duration": 90.0,
  "tempo": 117.5,
  "beats": [0.47, 0.98, ...],
  "downbeats": [0.47, 2.53, ...],
  "beat_energy": [0.12, 0.88, ...]
}
```

### 4.2 Timeline planning

Boundaries tile the track: `[0, cut_1, cut_2, ..., duration]`. A cut time is a beat
time, chosen every `beats_per_cut` beats (default 8), skipping guard bands
(first ~1.2 s, last ~1.5 s) and enforcing a minimum segment length.

Each segment shows `images[i % len(images)]`, so 3 images cycle through the track.

Crossfade timing (validated in the prototype) — with fade length `x` and boundary `b_i`:

- transition `i` starts at `offset_i = b_i - x` (clip `i` is fully visible by `b_i`)
- clip duration `d_i = (b_{i+1} - b_i) + x + slack` for all but the last
- last clip duration `= duration - offset_last`
- enforce `x ≤ 0.4 × shortest_segment` and `slack = 0.1 s`

This preserves audio sync: the total output is exactly `duration` and every transition
is centered on its beat.

v2: choose cuts from energy/sections (e.g. hold an image through a build, cut on the
drop) instead of a fixed bar count.

### 4.3 Visual system

Layers, bottom to top:

1. **Base image** — cover-crop to the aspect, supersampled 2×, Ken Burns zoom
   (`1.0 ↔ 1+zoom`), alternating direction each segment.
2. **Grade** — optional subtle contrast/saturation and vignette.
3. **Audio strip** — optional (see decision D3).
4. **HUD** — bottom gradient, title, optional artist. Pre-rendered as a transparent
   PNG with Pillow (Homebrew ffmpeg has no `drawtext`), faded in with alpha.
5. **Global fade** — 0.4 s in, 0.8 s out (video + audio).

Moves are intentionally few and reused: Ken Burns + beat-aligned crossfade cuts +
title fade. This is what makes it look intentional instead of random.

Per-aspect layout:

| | 9:16 (1080×1920) | 16:9 (1920×1080) |
|---|---|---|
| Title size | 8.5% of width | 7.5% of height |
| Title position | centered, block bottom at 80% height | left, block bottom at 87% height |
| Max text width | 86% | 55% |
| Artist | centered above title | left above title |
| Safe area | keep out of bottom ~15% / right ~12% | standard margins |

Text wraps to width using font metrics. Default font: `Arial Bold.ttf`; configurable via
`style.title_font`.

### 4.4 Render

Build one `ffmpeg -filter_complex` graph per aspect:

```
[image per segment] → scale 2× cover crop → zoompan (Ken Burns) → settb/setpts
→ xfade chain (beat offsets) → [optional audio strip]
→ HUD PNG overlay (alpha fade-in) → global fades → yuv420p
audio: atrim → loudnorm -14 LUFS → aresample 48k → afade
encode: libx264 crf 18, preset medium, AAC 192k, +faststart, yuv420p, 30 fps
```

Notes for the rebuild:

- HUD is a Pillow PNG because ffmpeg lacks `drawtext`.
- Force `aresample=48000` after `loudnorm` (prototype emitted 96 kHz).
- Supersample factor is configurable (2× improves zoom quality, costs speed).
- Temp files go in a per-render temp dir, always cleaned up.

## 5. CLI

```
vp analyze <track|root> [--force]      # write beatmap(s), no render
vp inspect <track>                     # print plan (cuts, segments, outputs) — dry run
vp render  <track> [--aspect both] [--preset clean] [--out DIR] [--force]
vp batch   <root>  [--aspect both] [--preset clean] [--out DIR] [--jobs N] [--force]
vp init    <track>                     # scaffold track.json / title.txt / images/
```

Common style flags: `--beats-per-cut`, `--crossfade`, `--zoom`, `--fps`, `--font`,
`--waveform/--no-waveform`.

Behavior:

- `--aspect`: `vertical`, `horizontal`, or `both` (default).
- `--force`: re-render even if outputs are newer than inputs.
- `--jobs`: tracks rendered concurrently (default 1; ffmpeg is already multithreaded).
- Batch isolates failures, prints a `rendered n/m` summary, exits non-zero if any failed.
- Idempotency: skip when output mtime > newest input/beatmap mtime unless `--force`.

## 6. Outputs

```
out/<track-slug>/
  <track-slug>_9x16.mp4
  <track-slug>_16x9.mp4
```

Slug = lowercased title with non-alphanumerics collapsed to `-`. Override the root with
`--out`. Optionally copy the beatmap next to outputs for debugging (`--debug`).

## 7. Repo layout (target)

```
visualizer-pipeline/
  pyproject.toml
  README.md
  DESIGN.md
  vp/
    cli.py  track.py  analyze.py  plan.py  hud.py  render.py  config.py
  presets/
    clean.json  punchy.json          # v2
  tests/
    test_plan.py  test_track.py  test_render_smoke.py
  scripts/
    make_fixtures.py
  fixtures/                           # generated, gitignored
  out/                                # generated, gitignored
```

## 8. Testing

- **Unit:** timeline math (offsets, durations, total = duration, no negative offsets),
  text wrapping, discovery/precedence, slug/naming, cache invalidation.
- **Integration:** render `fixtures/d_short` for both aspects; assert with `ffprobe`:
  resolution, fps, duration ±0.1 s, audio stream present, faststart.
- **Smoke batch:** run `vp batch fixtures/` in CI-ish mode with a short track.

## 9. Performance

- Analysis is a one-time cost per track (cached; ~1–3 s).
- Rendering is dominated by `zoompan` + x264. Prototype: ~0.7× realtime for 9:16 at
  2× supersample. Target ≤ 1× realtime per aspect after tuning.
- Optimizations kept in reserve: lower supersample factor, `preset veryfast`,
  pre-scaled image cache, concurrent aspects.

## 10. Known prototype issues to fix in the rebuild

- `showwaves` overlay path SIGKILLs ffmpeg → drop or replace (see D3).
- `loudnorm` → 96 kHz output → add `aresample=48000`.
- Homebrew ffmpeg has no `drawtext`/`subtitles` → keep Pillow HUD.
- Synthetic fixtures mis-detect tempo → document manual `overrides.bpm`.
- Horizontal output not yet verified end-to-end.

## 11. Decisions to lock

- **D1 — Visual preset.** Keep v1 to Ken Burns + beat crossfades + title fade? Or add a
  downbeat "punch"/flash from the start?
  *Recommend: ship the clean set in v1; add punch/flash as a `punchy` preset in v2.*
- **D2 — Cut strategy.** Fixed `beats_per_cut` (default 8) for v1, section-aware later?
  *Recommend: yes — fixed first, because it is predictable and testable.*
- **D3 — Audio strip.** Drop for v1, or build a Pillow-rendered scrolling waveform?
  *Recommend: drop in v1; add a Pillow strip in v2 (deterministic, no filter crashes).*
- **D4 — Video snippets.** Images only in v1; add `clips/` as timeline segments in v2?
  *Recommend: design the segment type now (image | video), implement images first.*
- **D5 — Text.** Title + optional artist only. Uppercase by default or preserve case?
  *Recommend: preserve case; presets may uppercase.*
- **D6 — Naming.** `<slug>_9x16.mp4` / `<slug>_16x9.mp4`? Or `__vertical`/`__youtube`?
  *Recommend: the former — short and platform-neutral.*
- **D7 — Tempo override.** `overrides.bpm` + `tempo_multiplier`? Also a `--bpm` flag?
  *Recommend: manifest override + `--bpm` CLI flag.*
- **D8 — Batch concurrency.** Default `--jobs 1`?
  *Recommend: yes; expose the flag for power users.*
- **D9 — Loudness.** `-14 LUFS`, true peak `-1.5 dBTP`?
  *Recommend: yes — matches platform normalization.*
- **D10 — FPS.** 30 default, 60 optional?
  *Recommend: 30 default.*

## 12. Milestones

- **M0 — Prototype (done):** discovery, librosa analysis, ffmpeg Ken Burns render, CLI,
  fixtures.
- **M1 — This design:** lock D1–D10.
- **M2 — Core rebuild:** config/presets module, pure `plan.py`, `hud.py`, hardened
  `render.py`, CLI + idempotency, unit + integration tests.
- **M3 — Polish:** presets, `inspect`, better progress output, section-aware cuts,
  performance tuning, README.
- **M4 — Expansion:** video snippets, scrolling waveform, optional 3D/texture overlays.