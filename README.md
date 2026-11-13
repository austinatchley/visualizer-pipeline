# visualizer-pipeline

[![CI](https://github.com/austinatchley/visualizer-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/austinatchley/visualizer-pipeline/actions/workflows/ci.yml)

Turn a beat plus a few stills into beat-synced visualizers from the command line:

- **16:9** full-track visualizer for YouTube
- **9:16** and **1:1** short hooks for Reels / Shorts / feed
- Energy-reactive cuts (hold through quiet sections, cut faster on drops)
- Batch a whole folder of beats, deterministically

Built for macOS + Homebrew ffmpeg, but the pipeline itself is plain Python + FFmpeg.

## Requirements

- Python 3.11+ (3.12 recommended)
- `ffmpeg` and `ffprobe` on `PATH`
- A system font (Arial Bold is used by default) or `style.title_font`

> The Homebrew ffmpeg build does not include `drawtext`; the HUD is rendered with
> Pillow, so no font/filter package is required.

## Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development (tests, including the web UI):

```bash
pip install -e ".[dev,ui]"
```

For the local web UI (optional):

```bash
pip install -e ".[ui]"
```

## Quickstart

```bash
vp init tracks/midnight-drive
# drop audio.wav into tracks/midnight-drive/
# drop 01.jpg / 02.jpg / 03.jpg into tracks/midnight-drive/images/
vp inspect tracks/midnight-drive   # review cuts, hook window, outputs
vp render  tracks/midnight-drive   # render every variant
```

Batch a catalog:

```bash
vp batch tracks --jobs 2
```

## Track folder

```
tracks/midnight-drive/
  audio.wav            # or mp3/flac/m4a/aiff/ogg; exactly one
  title.txt            # OR track.json -> "title"
  images/              # OR images in the folder root
    01.jpg
    02.jpg
    03.jpg
  track.json           # optional manifest
```

- Audio: the first matching file in the folder root (more than one requires `audio`).
- Images: `images/` if present, else image files in the root; sorted by filename and
  cycled through the edit. One or more required; three is the expected default.
- Title: `track.json.title` → `title.txt` → cleaned folder name.
- Originals are never modified; cover-cropping happens at render time per aspect.

## Manifest (`track.json`)

Everything is optional. Only include what differs from the defaults.

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
    "supersample": 2,
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
    "cuts": [12.0, 28.5, 41.0],
    "hook": { "start": 32.0, "end": 76.0 }
  }
}
```

### Style keys

| Key | Default | Notes |
|---|---|---|
| `preset` | `clean` | `clean`, `punchy`, or `balanced` |
| `beats_per_cut` | `8` | Base interval; energy-reactive cuts scale it |
| `crossfade` | `0.35` | Seconds; clamped to 40% of the shortest segment |
| `zoom` | `0.10` | Ken Burns amount (`1.0 → 1.1`) |
| `fps` | `30` | `24`, `30`, or `60` |
| `supersample` | `2` | Pre-zoom upscale factor (1–4); higher = sharper/slower |
| `contrast` | `1.0` | Luma contrast (`0.5–2.0`); `punchy` uses `1.15` |
| `beat_punch` | `0.0` | Extra zoom at each cut/downbeat that decays (`0–0.3`) |
| `vignette` | `0.0` | Edge darkening strength (`0–1`); `0` = off |
| `grain` | `0.0` | Film-grain amount (`0–1`); `0` = off, fixed seed |
| `texture` | `null` | Path to an image blended over the video |
| `texture_blend` | `multiply` | `multiply`, `overlay`, `screen`, or `softlight` |
| `waveform` | `false` | Scrolling Pillow waveform strip at the bottom |
| `title_font` | system Arial Bold | Path to a `.ttf` |

Presets only set style keys and can be overridden per-track or per-command:

- **`clean`** — the v1 defaults: calm Ken Burns, energy-reactive crossfades.
- **`punchy`** — shorter cuts, a decaying scale punch on each downbeat, higher contrast.
- **`balanced`** — a middle ground between the two.

### Hook

Vertical and square exports are short excerpts; horizontal is always the full track.

- `mode: "auto"` picks a 30–60s high-energy window (default target 45s).
- `mode: "full"` disables trimming (vertical/square become full-length).
- `start`/`end` (or `overrides.hook`, or `--hook START-END`) force an exact window.
- Tracks shorter than the target render full-length; their filenames omit `_hook`.

### Overrides

- `bpm` — re-run beat tracking with this tempo prior (fixes half/double-time reads).
- `tempo_multiplier` — `0.5`, `1`, or `2`; adjusts the beat grid after analysis.
- `cuts` — absolute seconds; replaces the automatic cut list.
- `hook` — absolute `{start, end}` seconds.

Precedence: defaults → preset → `track.json.style` → CLI flags.

## Commands

```bash
vp init    <track>                     # scaffold folder, images/, track.json
vp analyze <track> [--bpm N] [--tempo-multiplier 0.5|1|2]
vp inspect <track>                     # print style, hook window, cuts, outputs
vp render  <track> [options]           # render one track
vp batch   <root>  [options]           # render every track folder under root
vp ui      [options]                   # launch the local web studio (dark theme)
```

Common options for `inspect`, `render`, and `batch`:

```
--aspect vertical|horizontal|square|all   (default: all)
--out DIR                                 output root (default: out/)
--preset NAME                              clean|punchy|balanced
--beats-per-cut N  --crossfade S  --zoom F  --fps N  --supersample N
--contrast F  --beat-punch F
--vignette F  --grain F  --texture PATH  --texture-blend MODE
--font PATH  --handle @name
--hook auto|full|START-END
--bpm N  --tempo-multiplier 0.5|1|2
--waveform / --no-waveform
```

`render` and `batch` also accept:

```
--force        re-render even when outputs are up to date
--reanalyze    ignore the cached beatmap
--jobs N       batch concurrency (default 1; ffmpeg is already multi-threaded)
```

Renders are idempotent: an output is skipped when it is newer than the audio, images,
manifest, and beatmap.

## Local web UI

The CLI stays the headless core; `vp ui` is a thin client over the same engine
(config, resolution, rendering), so anything it produces matches a CLI run exactly.

```bash
pip install -e ".[ui]"
vp ui --tracks tracks      # opens a local dark-theme studio
```

`vp ui` options: `--host`, `--port` (default `127.0.0.1:8321`), `--tracks DIR`
(default `tracks/`), `--out DIR` (default `out/`), `--no-browser`.

The studio has a track picker, a **New Project** dialog that scaffolds a track from
dragged-in (or browsed) audio + images (validated against the same extensions as the
CLI), a style panel whose controls are generated from the same `STYLE_FIELDS` schema
that validates config (so new style keys appear automatically), a fast
quarter-resolution preview (short, audio-less), and an explicit "Save style" that writes
only the diff back to `track.json`.

Full renders run as background **jobs** with live progress (streamed from ffmpeg), an
up-to-date skip that matches the CLI's idempotency exactly, cancel, and in-browser
playback of finished videos. The **Compare A/B** flow snapshots two styles and renders
them side by side.

## Outputs

```
out/<track-slug>/
  <slug>_16x9.mp4        # full track
  <slug>_9x16_hook.mp4   # 30–60s excerpt
  <slug>_1x1_hook.mp4    # 30–60s excerpt
```

If a track is shorter than the hook target, vertical/square render full-length as
`<slug>_9x16.mp4` / `<slug>_1x1.mp4`. A custom `--out` nests by slug:
`<out>/<slug>/...`.

## How the edit is made

1. **Analyze** (`librosa`): tempo, beats, downbeats, per-beat energy, onset novelty,
   bar/phrase energy. Cached at `<track>/.vp/beatmap.json`.
2. **Plan** (pure Python): energy-reactive cut selection (section/drop boundaries plus
   an energy-scaled interval), hook-window scoring, and crossfade timing that keeps cuts
   on the beat.
3. **HUD** (Pillow): bottom gradient, title, artist, and handle per aspect.
4. **Render** (FFmpeg): cover-crop + Ken Burns, beat-aligned crossfades, HUD overlay,
   -14 LUFS audio, 48 kHz AAC, H.264 (yuv420p, faststart).

Full internals and decisions live in [`DESIGN.md`](DESIGN.md).

## Development

```bash
python scripts/make_fixtures.py   # generate 4 synthetic test tracks (gitignored)
pytest                            # unit + CLI + ffprobe integration tests
```

The integration tests skip if ffmpeg is missing or `fixtures/` has not been generated.
`fixtures/` and `out/` are gitignored.

## Contributing

Every change starts with an issue. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the
branch/commit/PR flow and checklist. Issue and PR templates live in `.github/`.

## Troubleshooting

- **Tempo detected half/double** — pass `--bpm` or set `overrides.bpm`; alternatively
  `overrides.tempo_multiplier: 0.5|2`.
- **No font found** — set `style.title_font` or pass `--font /path/to/font.ttf`.
- **Hook not applied** — the track may be shorter than the target, or `hook.mode` is
  `full`. Check `vp inspect`.
- **Renders too slow** — lower `--supersample` to 1 or use a faster x264 preset in a
  future release (see issue #12).

## Status

v1 plus the post-v1 local web studio are shipped. CI installs `.[dev,ui]`, generates
fixtures, and runs the full suite (86 tests at the time of writing).

Open work is tracked in
[issues](https://github.com/austinatchley/visualizer-pipeline/issues):

- #11 advanced section/phrase detection
- #12 performance tuning
- #14 video snippets
- #19 vibe-aware preset selection
- #20 `saturation`/`grade` style keys
- #21 beat-reactive `color_punch`
- #23–#25 web studio follow-ups (jobs/A-B, drag-and-drop reorder, autosave)