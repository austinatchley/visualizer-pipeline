# AGENTS.md

Runbook for AI agents and contributors. Read this first, then `DESIGN.md` for the
contract and `README.md` for user-facing usage. This file is the durable context for
resuming work in a fresh session.

## What this is

A CLI-first pipeline that turns a beat plus a few stills into:

- **16:9** full-track visualizer (YouTube)
- **9:16** and **1:1** 30–60 s hooks (Reels / Shorts / feed)

Cuts are energy-reactive and beat-aligned; renders are deterministic and idempotent.
A post-v1 FastAPI web studio (`vp ui`) wraps the same engine.

## Current state (snapshot)

- v1 + post-v1 web studio shipped. See `DESIGN.md` §11 (decisions) and §12 (milestones).
- **141 tests** pass locally with `.[dev,ui]`; CI runs the same suite on Ubuntu.
- CI: `.github/workflows/ci.yml` installs ffmpeg + DejaVu fonts, `.[dev,ui]`, generates
  fixtures, then runs `pytest -q`.
- Open issues: **#11** advanced sections, **#12** performance, **#14** video snippets,
  **#19** vibe-aware presets, **#20/#21** color keys, **#23–#25** web studio follow-ups,
  **#29** Lit componentization, **#37** VGM preset.
- Recent history: energy-reactive planner, hook windows, 3 aspects, Pillow HUD/waveform,
  presets, overlays, web UI, render jobs + cancellation, bundled OFL fonts + text
  controls, texture library + studio picker, finer waveform dynamics.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ui]"          # .[dev] alone runs non-UI tests; UI tests skip
python scripts/make_fixtures.py     # 4 synthetic tracks in fixtures/ (gitignored)
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

Useful flags: `--preset clean|punchy|balanced`, `--hook auto|full|START-END`,
`--bpm`, `--tempo-multiplier 0.5|1|2`, `--force`, `--reanalyze`, `--out DIR`,
`--supersample`, `--waveform/--no-waveform`.

## Architecture map

| Path | Responsibility |
|---|---|
| `vp/track.py` | Track discovery, manifest, title precedence, resolved options |
| `vp/config.py` | Defaults, `STYLE_FIELDS`, presets, hook/override resolution |
| `vp/analyze.py` | librosa analysis, cache, BPM/multiplier overrides |
| `vp/plan.py` | Pure planner: energy-reactive cuts, hook window, crossfade timing |
| `vp/hud.py` | Pillow HUD (gradient, title, artist, handle) per aspect |
| `vp/fonts.py` | Bundled OFL font registry, font resolution, variable-axis loading |
| `vp/textures.py` | Texture library discovery + library/custom path resolution |
| `vp/waveform.py` | Pillow scrolling waveform strip |
| `vp/render.py` | FFmpeg graph, variants, preview, progress/cancel, encoding |
| `vp/status.py` | Shared idempotency helpers (CLI + UI) |
| `vp/cli.py` | `analyze`, `inspect`, `render`, `batch`, `init`, `ui` |
| `vp/ui/` | FastAPI server + static studio (track picker, editor, jobs, A/B) |
| `presets/*.json` | `clean`, `punchy`, `balanced` style overrides |
| `textures/*.png` | Starter texture library (dropdown choices in the studio) |
| `tests/` | Unit, CLI, ffprobe, waveform, and UI tests |
| `scripts/make_fixtures.py` | Deterministic synthetic tracks for tests |
| `scripts/make_textures.py` | Deterministic starter texture generator |

## Invariants and quirks

- **One style schema.** Add a key to `DEFAULT_STYLE` *and* `config.STYLE_FIELDS`; validation
  and the UI controls both derive from it. Presets may only contain style keys.
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
- **Idempotency.** `vp/status.py` decides skips; CLI and UI share it. `--force` overrides.
- **Preview/jobs.** `render(..., preview=True)` is a short, quarter-res, audio-less clip;
  the UI renders all three aspects side by side, slicing around a real cut so at least
  one transition is visible. `progress=`/`cancel=` support the UI job runner.
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

## Suggested next steps

1. #37 VGM-style preset (uses bundled fonts; coordinate grade keys with #20).
2. #11 advanced section/phrase detection (feeds better cuts).
3. #19 vibe-aware preset selection (uses `style.preset: "auto"`).
4. #12 performance tuning (supersample/x264/variant parallelism).
5. #14 video snippet segments.
6. #20/#21 color grading keys and beat-reactive color swings.
7. #23–#25 web studio follow-ups.
