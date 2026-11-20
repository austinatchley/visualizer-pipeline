# AGENTS.md

Runbook for AI agents and contributors. Read this first, then `DESIGN.md` for the
contract and `README.md` for user-facing usage. This file is the durable context for
resuming work in a fresh session.

## What this is

`os.10.vs` is a CLI-first visualizer studio that turns a beat plus a few stills into:

- **16:9** full-track visualizer (YouTube)
- **9:16** and **1:1** 30–60 s hooks (Reels / Shorts / feed)

Cuts are energy-reactive and beat-aligned; renders are deterministic and idempotent.
A post-v1 FastAPI web studio (`vp ui`) wraps the same engine.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ui]"          # .[dev] alone runs non-UI tests; UI tests skip
python scripts/make_fixtures.py     # 4 synthetic tracks in fixtures/ (gitignored)
ruff check . && ruff format --check .   # CI lint job; ruff is pinned in [dev]
npm ci && npm run typecheck             # CI typecheck job: tsc over the studio JS
python scripts/gen_api_types.py         # after changing vp/ui/models.py: openapi.json + api.d.ts
pytest -q
```

Requirements: Python 3.12, `ffmpeg`/`ffprobe` on `PATH`, a system font (macOS Arial Bold
or Linux DejaVu) or `style.title_font`.

## Commands

```bash
vp init    tracks/foo                 # scaffold track.json + images/
vp analyze tracks/foo [--bpm N]       # write/refresh .vp/beatmap.json
vp inspect tracks/foo                 # cuts, hook window, style, output paths
vp render  tracks/foo [--aspect all]  # render variants
vp batch   tracks --jobs 2            # render every track folder
vp ui --tracks tracks                 # local web studio (optional [ui] extra)
```

Useful flags: `--preset datahud|vgm`, `--hook auto|full|START-END`,
`--bpm`, `--tempo-multiplier 0.5|1|2`, `--force`, `--reanalyze`, `--out DIR`, plus one
flag per style key (`--font anton`, `--title-font PATH`, `--spectrum`, ...; see
`vp render --help`).

## Architecture map

| Path | Responsibility |
|---|---|
| `vp/manifest.py` | `track.json` read/atomic write, track-folder discovery, image dir/scan (shared by CLI + UI) |
| `vp/track.py` | Track loading, title precedence, resolved options |
| `vp/config.py` | Defaults, `STYLE_FIELDS`, presets, hook/override resolution |
| `vp/analyze.py` | librosa analysis, cache, BPM/multiplier overrides |
| `vp/plan.py` | Pure planner: energy-reactive cuts, hook window, crossfade timing |
| `vp/hud.py` | Pillow HUD (gradient, title, artist, handle) + VGM watermark overlay per aspect |
| `vp/fonts.py` | Bundled OFL font registry, font resolution, variable-axis loading |
| `vp/textures.py` | Texture library discovery + library/custom path resolution |
| `vp/audio.py` | ffmpeg mono decode shared by the reactive layers |
| `vp/spectrum.py` | Pure numpy per-frame log-frequency spectrum (filterbank, normalization, ballistics, trails) |
| `vp/reactive.py` | Per-frame reactive layers: parallel Pillow drawing → one FFV1+alpha clip per layer, overlaid by `build_graph` |
| `vp/readouts.py` | Readout strip (clock, beat cells, BPM/key, band meters): per-frame state + painter |
| `vp/schematic.py` | Schematic background: seeded board generation, static linework PNG, per-frame node glows |
| `vp/music.py` | Pure note naming and key detection from chroma (relative-minor tie-break) |
| `vp/render.py` | `prepare_assets` (PNGs) → pure `build_graph`/`build_command` → `run_ffmpeg`; variants, preview |
| `vp/status.py` | Render fingerprints + `.vp.json` sidecars for idempotency (CLI + UI) |
| `vp/cli.py` | `analyze`, `inspect`, `render`, `batch`, `init`, `ui` |
| `vp/ui/` | FastAPI server + static studio (track picker, editor, jobs, A/B) |
| `vp/ui/types/` | Dev-only `tsc` types: generated `openapi.json` + `api.d.ts`, hand-written `studio.d.ts` (aliases + client state) |
| `scripts/gen_api_types.py` | Regenerates `vp/ui/types/openapi.json` and `api.d.ts` from the Pydantic models |
| `vp/ui/models.py` | Pydantic request/response models; `Style` is generated from `STYLE_FIELDS` (the `/openapi.json` contract) |
| `presets/*.json` | Style overrides on the built-in `datahud` default (`vgm`) |
| `textures/*.png` | Starter texture library (dropdown choices in the studio) |
| `tests/` | Unit, CLI, ffprobe, reactive-layer, and UI tests |
| `scripts/make_fixtures.py` | Deterministic synthetic tracks for tests |
| `scripts/make_regression_tracks.py` | Arranged genre-shaped beats with known BPMs + sections; `tests/test_regression_audio.py` synthesizes them on the fly |
| `scripts/make_textures.py` | Deterministic starter texture generator |

## Invariants and quirks

- **One style schema.** Add a key to `DEFAULT_STYLE` *and* `config.STYLE_FIELDS`; validation,
  the UI controls, and the CLI flags all derive from it. Presets may only contain style keys.
- **The default is the product.** `DEFAULT_STYLE` is the data-HUD look (`datahud`); a
  track with no options must render postable output. New layers default *on*, and every
  change to the default look gets checked on the real-beat fixtures, not only the
  synthetic ones. `presets/*.json` (`vgm`) are overrides on top of it. Removed presets go
  in `config.RETIRED_PRESETS` (they warn and fall back) and removed keys in
  `RETIRED_STYLE_KEYS`.
- **Aspects.** 16:9 is always full-length; 9:16 and 1:1 use hooks. Hooks are 30–60 s
  (default 45) and auto-scored; short tracks render full and omit the `_hook` suffix.
- **Audio.** `loudnorm -14 LUFS`, true peak -1.5 dBTP, then `aresample=48000`. AAC 192k.
  The 48 kHz choice is the video-deliverable convention, not a quality requirement.
- **Video.** H.264 CRF 18, 30 fps, `yuv420p` with TV range (`scale=out_range=tv`),
  `+faststart`.
- **Cuts.** Energy-reactive (sections/drops + energy-scaled interval); `overrides.cuts`
  replaces them. Crossfade is clamped to 40% of the shortest segment.
- **HUD.** Pillow PNG because Homebrew ffmpeg lacks `drawtext`. Do not reintroduce
  `drawtext`/`subtitles`.
- **Typography.** `vp/fonts.py` is the OFL face registry; `font`/`meta_font`,
  `text_case`, `tracking`, and `line_spacing` are style keys. `title_font` is an
  advanced path override. Licenses live in `vp/assets/fonts/licenses/`. Readouts, note
  callouts, and spectrum axis labels always use `fonts.READOUT_FONT` (JetBrains Mono).
- **Textures.** `vp/textures.py` discovers the `textures/` library and resolves relative
  `style.texture` names; the studio validates dropdown choices. CLI `--texture PATH`
  still accepts custom image files.
- **Retired style keys.** `config.RETIRED_STYLE_KEYS` lists removed keys (`waveform`).
  A `track.json` that still sets one loads with a warning and the key is dropped; never
  reintroduce `showwaves` (it crashed ffmpeg) or the scrolling strip.
- **Track identity.** `Track.id` is the folder name: output dir, output filenames, studio
  routes, and jobs all key on it. Titles are display-only; never derive paths from them.
- **Idempotency.** `vp/status.py` fingerprints every render input (style, HUD text,
  window, beatmap, file identities) into an `<output>.vp.json` sidecar; CLI and UI skip
  only on a match. `--force` overrides. **Bump `status.RENDER_VERSION`** when a code change
  alters rendered output for the same inputs.
- **Render graph snapshots.** `tests/snapshots/render_graphs.json` pins the ffmpeg
  command per preset/aspect/feature. An intended graph change: regenerate with
  `UPDATE_SNAPSHOTS=1 pytest tests/test_render_graph.py`, review the diff, and bump
  `RENDER_VERSION`.
- **Preview/jobs.** `render(..., preview=True)` is a short, quarter-res, audio-less clip;
  the UI renders all three aspects side by side, slicing around a real cut so at least
  one transition is visible. `progress=`/`cancel=` support the UI job runner.
  The studio has two render lanes (`_export_lock`, `_preview_lock`): exports run one at a
  time, previews run one at a time, and a preview never waits behind an export.
- **Studio frontend.** Plain JS served as-is (no build step), type-checked with
  `// @ts-check` + `tsconfig.json` (`strict`). Annotate API results with the response
  type (`/** @type {TrackDetail} */ const t = await api(...)`); `$`/`pick` infer the
  element type from the annotated variable. API types are **generated** from
  `vp/ui/models.py`: change a model → `python scripts/gen_api_types.py` → commit both
  files. A test (stale `openapi.json`) and the CI typecheck job (stale `api.d.ts`) enforce it.
- **Reactive layers.** Anything that moves with the music per frame is drawn in
  `vp/reactive.py` (not ffmpeg filters), one clip per layer covering that layer's box
  (schematic nodes, spectrum, readout strip; a layer may draw at reduced resolution and
  set `scale_to`). Static linework (the schematic traces) is a PNG, not a reactive layer.
  Painters load fonts lazily and drop them when pickled. Drawing
  must be a pure function of the frame index: the process pool's output is tested to be
  byte-identical to the serial path. The pool uses forkserver/spawn, never fork (the
  studio renders from threads).
- **Determinism.** No random effects without a fixed seed (grain uses one).

## Current work and next steps

The data-HUD epic is **#80**; its plan is in `DESIGN.md` §12 (M5). Direction: the
reactive data-HUD is the *default* look (no options set → ready to post for most
beats), and features that don't serve it are removed. Done: the engine and spectrum
layer (#81), overlays above the grade (#86), the waveform removal (#87), and the
data-HUD default (#88), the readouts (#82), and the schematic background (#83). Next:
clips (#14). Features
stop for the user's review before merging. Test on the user's real beats as well as the
synthetic regression tracks: copy them into the track folder layout under the
gitignored `fixtures/` and never modify the originals.

## Workflow (issue-first)

Every change starts with an issue. See `CONTRIBUTING.md`.

- Branch: `<issue-number>-<short-slug>` (e.g. `9-ci`).
- Commits: imperative subject, `Refs #N` while in progress, `Closes #N` on the final
  commit.
- PRs: `Closes #N`, pass CI, complete the PR template checklist.
- Keep `main` green. Generated media (`fixtures/`, `out/`, `.venv/`) is gitignored.

## Docs

- `README.md` — user-facing install, manifest, commands, UI, troubleshooting.
- `DESIGN.md` — contract, pipeline math, decisions (§11), milestones (§12).
- `CONTRIBUTING.md` — issue-first workflow and checklist.
- `AGENTS.md` — this runbook.

## Known gotchas

- The synthetic fixtures mis-detect tempo (e.g. 140 → 92.3); use `--bpm` or
  `overrides.tempo_multiplier` to test tempo paths. For realistic analysis/planner
  checks use the regression tracks (`tests/test_regression_audio.py`): they assert
  BPM (±3%, octaves allowed), hook-on-drop, and a cut at every drop entry. Planner
  changes that fail there are real regressions, not fixture noise.
- UI tests `importorskip` FastAPI/httpx; install `.[dev,ui]` to run them.
- Starlette currently warns that `httpx` is deprecated for `TestClient`; tests still pass.
- `fixtures/` must exist before render/integration tests; otherwise they skip.
