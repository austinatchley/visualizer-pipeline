# Visualizer Pipeline — Design

Status: **v1 scope locked** (prototype exists; this document is the contract for the rebuild)

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

1. **Base image** — cover-crop to the aspect, supersampled 2×, Ken Burns zoom
   (`1.0 ↔ 1+zoom`), alternating direction each segment.
2. **Grade** — optional subtle contrast/saturation and vignette.
3. **Audio strip** — optional (see D3).
4. **HUD** — bottom gradient, title, and a smaller meta line (`artist · handle`).
   Pre-rendered as a transparent PNG with Pillow (Homebrew ffmpeg has no `drawtext`),
   faded in with alpha.
5. **Global fade** — 0.4 s in, 0.8 s out (video + audio).

Moves are intentionally few and reused: Ken Burns + beat/energy-aligned crossfade cuts +
title fade. This is what makes it look intentional instead of random.

**Presets and vibe.** `clean` is the v1 default (Ken Burns, crossfades, restrained type).
`punchy` and a combined preset are added incrementally (M3), and `style.preset` lets each
track pick a look. Later, `style.preset: "auto"` maps audio features (tempo band, energy
mean/variance, low/high balance) to a preset so the visuals follow the vibe of the music.
Explicit presets always win over `auto`.

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
[image per segment] → scale 2× cover crop → zoompan (Ken Burns) → settb/setpts
→ xfade chain (beat offsets) → [optional audio strip]
→ HUD PNG overlay (alpha fade-in) → global fades → yuv420p
audio: atrim(window) → loudnorm -14 LUFS → aresample 48k → afade
encode: libx264 crf 18, preset medium, AAC 192k, +faststart, yuv420p, 30 fps
```

Variants: `16x9` full, `9x16` hook, `1x1` hook. Hooks trim both video and audio to the
selected window.

Notes for the rebuild:

- HUD is a Pillow PNG because ffmpeg lacks `drawtext`.
- Force `aresample=48000` after `loudnorm` (prototype emitted 96 kHz).
- Supersample factor is configurable (2× improves zoom quality, costs speed).
- Temp files go in a per-render temp dir, always cleaned up.

## 5. CLI

```
vp analyze <track|root> [--force]      # write beatmap(s), no render
vp inspect <track>                     # print plan: cuts, windows, style, outputs
vp render  <track> [--aspect all] [--preset clean] [--hook auto] [--out DIR] [--force]
vp batch   <root>  [--aspect all] [--preset clean] [--hook auto] [--out DIR] [--jobs N]
vp init    <track>                     # scaffold track.json / title.txt / images/
```

Common flags: `--beats-per-cut`, `--crossfade`, `--zoom`, `--fps`, `--font`, `--handle`,
`--bpm`, `--waveform/--no-waveform`.

Behavior:

- `--aspect`: `vertical`, `horizontal`, `square`, or `all` (default: all).
- `--hook`: `auto` (default), `full` (no trimming), or `START-END` seconds.
- `--force`: re-render even if outputs are newer than inputs.
- `--jobs`: tracks rendered concurrently (default 1; ffmpeg is already multithreaded).
- Batch isolates failures, prints a `rendered n/m` summary, exits non-zero if any failed.
- Idempotency: skip when output mtime > newest input/beatmap mtime unless `--force`.

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
  vp/
    cli.py  track.py  analyze.py  plan.py  hud.py  render.py  config.py
  presets/
    clean.json                        # v1
    punchy.json                       # M3
  tests/
    test_plan.py  test_track.py  test_hud.py  test_render_smoke.py
  scripts/
    make_fixtures.py
  fixtures/                           # generated, gitignored
  out/                                # generated, gitignored
```

## 8. Testing

- **Unit:** timeline math (offsets, durations, total = window, no negative offsets),
  energy-reactive cut selection, hook scoring/rebasing, text wrapping/layout,
  discovery/precedence, slug/naming, cache invalidation.
- **Integration:** render `fixtures/d_short` for all variants; assert with `ffprobe`:
  resolution (1080×1920, 1080×1080, 1920×1080), fps, duration ±0.1 s, 48 kHz audio,
  faststart.
- **Smoke batch:** run `vp batch fixtures/` with a short track.

## 9. Performance

- Analysis is a one-time cost per track (cached; ~1–3 s).
- Rendering is dominated by `zoompan` + x264. Prototype: ~0.7× realtime for 9:16 at
  2× supersample. Target ≤ 1× realtime for the full 16:9 render.
- Hook exports (9:16 / 1:1) are cheaper because they only render 30–60 s.
- Optimizations kept in reserve: lower supersample factor, `preset veryfast`,
  pre-scaled image cache, concurrent variants.

## 10. Known prototype issues to fix in the rebuild

- `showwaves` overlay path SIGKILLs ffmpeg → drop or replace (see D3).
- `loudnorm` → 96 kHz output → add `aresample=48000`.
- Homebrew ffmpeg has no `drawtext`/`subtitles` → keep Pillow HUD.
- Synthetic fixtures mis-detect tempo → document manual `overrides.bpm`.
- Horizontal output not yet verified end-to-end.

## 11. Decisions (locked)

- **D1 — Visual preset.** Start with `clean` (Ken Burns + beat/energy crossfades + title
  fade). Add `punchy` and a combined preset incrementally; per-track selection in M3.
- **D2 — Cut strategy.** Energy-reactive in v1: hold through low-energy sections, cut
  faster on high-energy/drops. Fixed interval is the fallback and the escape hatch.
- **D3 — Audio strip.** Drop for v1; deterministic Pillow waveform in M4.
- **D4 — Video snippets.** Images only in v1; `clips/` segments in M4.
- **D5 — Text.** Title + artist + handle, preserve case, bold sans, shadow.
- **D6 — Naming.** `<slug>_16x9.mp4`, `<slug>_9x16_hook.mp4`, `<slug>_1x1_hook.mp4`.
- **D7 — Tempo override.** `overrides.bpm` + `tempo_multiplier` + `--bpm`.
- **D8 — Batch concurrency.** Default `--jobs 1`; expose the flag.
- **D9 — Loudness.** -14 LUFS integrated, -1.5 dBTP true peak.
- **D10 — FPS.** 30 default; 60 optional later.
- **D11 — Length/versioning.** 16:9 is the full track; 9:16 and 1:1 are 30–60 s hooks
  (default 45 s), auto-selected or manually overridden.
- **D12 — Formats.** 9:16, 1:1, and 16:9 at 30 fps.
- **D13 — Vibe tailoring.** Per-track `style.preset` now; `style.preset: "auto"` maps
  audio features to a preset in M3, with explicit presets always winning.

## 12. Milestones

- **M0 — Prototype (done):** discovery, librosa analysis, ffmpeg Ken Burns render, CLI,
  fixtures.
- **M1 — Design locked (this document).**
- **M2 — Core rebuild:** config/presets (clean), energy-reactive planner + hook windows,
  HUD (title/artist/handle), renderer (3 variants, 48 kHz, no waveform), CLI (aspects,
  hook, idempotency, jobs, inspect), unit + ffprobe tests, CI.
- **M3 — Polish:** `punchy`/combined presets, vibe-aware preset selection, advanced
  section/phrase detection, performance tuning, README.
- **M4 — Expansion:** video snippets, scrolling waveform, grain/vignette/texture.