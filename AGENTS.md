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

Useful flags: `--preset clean|punchy|balanced|vgm|auto`, `--hook auto|full|START-END`,
`--bpm`, `--tempo-multiplier 0.5|1|2`, `--force`, `--reanalyze`, `--out DIR`, plus one
flag per style key (`--font anton`, `--title-font PATH`, `--no-waveform`, ...; see
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
| `vp/waveform.py` | Pillow scrolling waveform strip |
| `vp/render.py` | `prepare_assets` (PNGs) → pure `build_graph`/`build_command` → `run_ffmpeg`; variants, preview |
| `vp/status.py` | Render fingerprints + `.vp.json` sidecars for idempotency (CLI + UI) |
| `vp/cli.py` | `analyze`, `inspect`, `render`, `batch`, `init`, `ui` |
| `vp/ui/` | FastAPI server + static studio (track picker, editor, jobs, A/B) |
| `vp/ui/types/` | Dev-only `tsc` types: generated `openapi.json` + `api.d.ts`, hand-written `studio.d.ts` (aliases + client state) |
| `scripts/gen_api_types.py` | Regenerates `vp/ui/types/openapi.json` and `api.d.ts` from the Pydantic models |
| `vp/ui/models.py` | Pydantic request/response models; `Style` is generated from `STYLE_FIELDS` (the `/openapi.json` contract) |
| `presets/*.json` | `clean`, `punchy`, `balanced` style overrides |
| `textures/*.png` | Starter texture library (dropdown choices in the studio) |
| `tests/` | Unit, CLI, ffprobe, waveform, and UI tests |
| `scripts/make_fixtures.py` | Deterministic synthetic tracks for tests |
| `scripts/make_textures.py` | Deterministic starter texture generator |

## Invariants and quirks

- **One style schema.** Add a key to `DEFAULT_STYLE` *and* `config.STYLE_FIELDS`; validation,
  the UI controls, and the CLI flags all derive from it. Presets may only contain style keys.
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
  advanced path override. Licenses live in `vp/assets/fonts/licenses/`.
- **Textures.** `vp/textures.py` discovers the `textures/` library and resolves relative
  `style.texture` names; the studio validates dropdown choices. CLI `--texture PATH`
  still accepts custom image files.
- **Waveform.** Pillow strip only, with ~4 px RMS/peak bars (95th-percentile
  normalization, soft curve, noise floor). The old `showwaves` filter path crashed ffmpeg.
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
- **Determinism.** No random effects without a fixed seed (grain uses one).

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
  `overrides.tempo_multiplier` to test tempo paths.
- UI tests `importorskip` FastAPI/httpx; install `.[dev,ui]` to run them.
- Starlette currently warns that `httpx` is deprecated for `TestClient`; tests still pass.
- `fixtures/` must exist before render/integration tests; otherwise they skip.
