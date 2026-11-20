# os.10.vs

[![CI](https://github.com/austinatchley/visualizer-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/austinatchley/visualizer-pipeline/actions/workflows/ci.yml)

**Visualizer studio by os.10.** Turn a beat plus a few stills into beat-synced visualizers from the command line:

- **16:9** full-track visualizer for YouTube
- **9:16** and **1:1** short hooks for Reels / Shorts / feed
- Energy-reactive cuts (hold through quiet sections, cut faster on drops)
- Batch a whole folder of beats, deterministically

Built for macOS + Homebrew ffmpeg, but the pipeline itself is plain Python + FFmpeg.

## Requirements

- Python 3.12+
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
    "preset": "datahud",
    "beats_per_cut": 8,
    "crossfade": 0.35,
    "zoom": 0.0,
    "fps": 30,
    "supersample": 2,
    "title_font": null,
    "font": "redaction",
    "meta_font": "clean",
    "text_case": "upper",
    "tracking": 0.02,
    "line_spacing": 1.12
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
| `preset` | `datahud` | `datahud` (the default look) or `vgm`. Retired names (`clean`, `punchy`, `balanced`, `auto`) fall back to `datahud` with a warning |
| `beats_per_cut` | `8` | Base interval; energy-reactive cuts scale it |
| `crossfade` | `0.35` | Seconds; clamped to 40% of the shortest segment |
| `fade_in` | `0.4` | Seconds of fade from black + audio fade-in at the start; `0` = off |
| `fade_out` | `0.8` | Seconds of fade to black + audio fade-out at the end; `0` = off. Both shrink proportionally on clips shorter than their sum |
| `zoom` | `0` | Ken Burns drift per cut (`1.0 → 1+zoom`, alternating in/out); `0` = still |
| `fps` | `30` | `24`, `30`, or `60` |
| `supersample` | `2` | Pre-zoom upscale factor (1–4); higher = sharper/slower |
| `contrast` | `1.0` | Luma contrast (`0.5–2.0`) |
| `saturation` | `1.0` | Color saturation (`0–2`); `0` renders true monochrome (and scales `color_punch` down with it) |
| `beat_punch` | `0.04` | Gentle zoom punch on big hits (loud, sharp beats) that settles in ~0.5 s (`0–0.3`) |
| `color_punch` | `0.2` | Saturation spike at each cut that decays over ~0.3s (`0–1`); baseline follows energy |
| `dim` | `0.15` | Darken the footage under the overlays (`0–0.6`); scales toward black, so bright skies drop most |
| `vignette` | `0.25` | Edge darkening strength (`0–1`); `0` = off |
| `grain` | `0.15` | Luma film-grain amount (`0–1`); `0` = off, fixed seed |
| `texture` | `null` | Texture name from `textures/` (or a custom image path) |
| `texture_blend` | `multiply` | `multiply`, `overlay`, `screen`, or `softlight` |
| `title_font` | system Arial Bold | Path to a `.ttf`; overrides `font` |
| `font` | `redaction` | Bundled face: `redaction`, `redaction-regular`, `redaction-italic`, `tiny5`, `tiny5-round`, `tiny5-jitter`, `pixelify`, `silkscreen`, `syne`, `instrument`, `anton`, `mono`, or `clean` |
| `meta_font` | `auto` | Face for artist/handle; `auto` matches the title |
| `text_case` | `preserve` | `preserve`, `upper`, or `lower` |
| `tracking` | `0.0` | Letter spacing in em (`-0.05`–`0.25`) |
| `line_spacing` | `1.12` | Title line-height multiplier (`1.0`–`1.5`) |
| `grade` | `none` | Film grade (`none`, `sunset`, `night`, `fog`, or `analog` — the warm poison cast) |
| `show_title` | `true` | Show the title/artist/handle HUD |
| `watermark` | `false` | Giant translucent watermark overlay (on/off) |
| `watermark_text` | `postbag` | Watermark text; `watermark` turns it on/off |
| `watermark_font` | `anton` | Watermark typeface (any bundled face) |
| `watermark_opacity` | `0.15` | Watermark opacity (`0–0.5`) |
| `watermark_width` | `0.95` | Watermark width as a fraction of frame width |
| `watermark_stroke` | `0.06` | Watermark outline thickness as a fraction of font size |
| `watermark_x` / `watermark_y` | `0.0` | Watermark offset (`-1..1`; `0` = center, magnitude bleeds off-frame) |
| `spectrum` | `true` | Music-reactive spectrum analyzer layer (log-frequency curve with trailing lines), drawn per frame. Adds a drawing pass: roughly +35% render time |
| `spectrum_height` | `0.22` | Plot height as a fraction of frame height (`0.05–0.6`) |
| `spectrum_y` | `1.0` | Plot vertical position: `0` top, `0.5` centered, `1` on the bottom edge |
| `backplate` | `0.6` | Soft dark plate behind the reactive layers so they read on bright images (`0–1`); `0` = off |
| `accent_color` | `#d9708f` | Accent for reactive layers (`#rrggbb`); the studio shows a color picker |
| `show_clock` | `true` | Readout: track position / total (`01:35 / 02:36`) |
| `show_tempo` | `true` | Readout: BPM and detected key (`99  C# MIN`) |
| `show_beats` | `true` | Readout: four beat-in-bar cells; the downbeat lights in the accent color |
| `show_meters` | `true` | Readout: sub / low / mid / high band meters |
| `show_notes` | `true` | Note callouts (`C#3 138Hz`) on the spectrum's strongest low peaks; needs `spectrum` |
| `schematic` | `true` | Circuit-trace background: seeded linework (one board per track, thinning toward the middle) whose nodes glow with the band meters and flash on downbeats |
| `schematic_density` | `0.5` | How many traces the board draws (`0.1–1`) |
| `schematic_opacity` | `0.24` | Linework opacity (`0–0.6`); `0` hides the traces, nodes still pulse |

The defaults *are* the look: a track with no `style` renders the **data-HUD**
(`datahud`). The footage is dimmed and graded underneath, and a music-reactive
spectrum sits on a soft backplate with note callouts on its peaks, over a faint
circuit-board schematic whose nodes pulse with the music. An instrument strip
across the top shows the clock, beat cells, BPM, key, and band meters in a bundled
monospace face (JetBrains Mono), with crisp text on top. It aims to be ready to post
for most beats without changes. Every key above is a knob for tuning to taste.

Presets are style overrides layered on the defaults, and can themselves be overridden
per track or per command:

- **`vgm`** — the data-HUD with a sunset grade and a giant translucent `postbag`
  watermark instead of the title: `grade: sunset`, `show_title: false`,
  `watermark: true`, `grain: 0.08`, `vignette: 0.2`.

### Fonts

Bundled under `vp/assets/fonts/` (all SIL OFL, licenses included):

- **Redaction 50** — Regular / Bold / Italic (rough, printed serif)
- **Tiny5 Duo** — Black plus variable Round/Jitter instances (chunky pixel display)
- **Pixelify Sans** — Bold pixel sans
- **Silkscreen** — classic pixel type
- **Syne** — ExtraBold experimental geometric
- **Instrument Serif** — editorial contrast
- **Anton** — condensed impact
- **JetBrains Mono** — SemiBold monospace (`mono`); the data readouts always use it

`clean` falls back to system Arial/DejaVu. Set `title_font` to a path to override the
dropdown for a specific track.

### Textures

`textures/` ships a starter library (`grain-35mm`, `dust-scratches`, `halftone`, `paper`,
`concrete`). Regenerate it deterministically with:

```bash
python scripts/make_textures.py
```

The studio's `texture` control is a dropdown populated from that library; `vp ui
--textures DIR` points at a different library. Relative `style.texture` names resolve
under `textures/`, while `vp render --texture PATH` still accepts a custom image file.

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
--handle @name
--hook auto|full|START-END
--bpm N  --tempo-multiplier 0.5|1|2
```

Every style key also has a flag: the key with dashes, e.g. `--beats-per-cut 4`,
`--grade analog`, `--font anton`, `--title-font /path/to/font.ttf`, `--fade-out 0`,
`--no-show-title`. Booleans take `--key` / `--no-key`. `vp render --help` lists them all
with their choices and ranges; they are generated from `config.STYLE_FIELDS`, so new
style keys get a flag automatically.

> **Changed:** `--font` now picks a bundled face (`anton`, `syne`, ...). The old
> `--font PATH` (a `.ttf` file) is now `--title-font PATH`.

`render` and `batch` also accept:

```
--force        re-render even when outputs are up to date
--reanalyze    ignore the cached beatmap
--jobs N       batch concurrency (default 1; ffmpeg is already multi-threaded)
```

`render` additionally accepts `--variant-jobs N` (default 1) to render aspects
concurrently — useful for `--aspect all`, where the 16:9 full render is the long pole and
the short hooks can run alongside it.

Renders are idempotent: each output gets a `<name>.mp4.vp.json` sidecar recording a
fingerprint of everything that shaped it (resolved style and preset, title/artist/handle,
aspect, hook window, beatmap, audio/image/texture/font files, renderer version). A rerun
skips an output only when the fingerprint matches, so changing `--preset`, a style flag,
a preset file, or a texture re-renders it. `--force` always re-renders.

## Local web UI

The CLI stays the headless core; **os.10.vs**, the `vp ui` web studio, is a thin client
over the same engine (config, resolution, rendering), so anything it produces matches a
CLI run exactly.

```bash
pip install -e ".[ui]"
vp ui --tracks tracks      # opens os.10.vs in the browser
```

`vp ui` options: `--host`, `--port` (default `127.0.0.1:8321`), `--tracks DIR`
(default `tracks/`), `--out DIR` (default `out/`), `--textures DIR` (default
`textures/`), `--no-browser`.

The studio has a track picker, a **New Project** dialog that scaffolds a track from
dragged-in (or browsed) audio + images (validated against the same extensions as the
CLI), a style panel whose controls are generated from the same `STYLE_FIELDS` schema
that validates config (so new style keys appear automatically), three-up previews for
9:16 / 1:1 / 16:9 (a 4 s slice around a cut, quarter-resolution, audio-less), and
**autosave**: style edits persist to `track.json` ~1 s after you stop tweaking (validated
through the same `resolve_style` path, so an invalid value never reaches disk), with a
**Reset to preset** action to discard changes. Textures are selected from the
`textures/` library instead of typing a path.

An existing track's **Images** strip lives at the top of the track view: add images from
disk, drag cards to reorder them (the order is written back to `track.json`), or remove a
card (deletes the file). The track header has inline **title / artist / handle** fields
that write back to `track.json`; the track keeps its folder-based id, so renaming never
moves outputs. **Delete
track** removes the whole folder after a confirmation; running jobs for that track are
cancelled first.

Full renders run as background **jobs** with live progress (streamed from ffmpeg), an
up-to-date skip that matches the CLI's idempotency exactly, per-job cancel for queued and
running renders, and in-browser playback of finished videos. The **Compare A/B** flow
snapshots two styles and renders a short preview clip of each side by side. A/B clips go
to `out/preview/<id>_<aspect>_<fingerprint>.mp4` (the newest 8 per track and aspect are
kept), so comparing never overwrites the real exports in `out/<id>/`.

## Outputs

```
out/<id>/
  <id>_16x9.mp4        # full track
  <id>_9x16_hook.mp4   # 30–60s excerpt
  <id>_1x1_hook.mp4    # 30–60s excerpt
```

If a track is shorter than the hook target, vertical/square render full-length as
`<id>_9x16.mp4` / `<id>_1x1.mp4`. `<id>` is the track's folder name, so two tracks with
the same title never collide and renaming a title keeps its outputs. A custom `--out`
nests by id: `<out>/<id>/...`.

## How the edit is made

1. **Analyze** (`librosa`): tempo, beats, downbeats, per-beat energy, onset novelty,
   bar/phrase energy. Cached at `<track>/.vp/beatmap.json` (or `beatmap-bpm<N>.json` with a
   BPM hint) and re-analyzed automatically when the audio file changes.
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
- **No font found** — set `style.title_font` or pass `--title-font /path/to/font.ttf`.
- **Hook not applied** — the track may be shorter than the target, or `hook.mode` is
  `full`. Check `vp inspect`.
- **Renders too slow** — the renderer runs at roughly realtime (a 161 s 16:9 render takes
  ~157 s on a 16-core box). `--supersample 1` is ~33% faster but slightly softer; the x264
  preset barely matters because the `zoompan` filter dominates. Use `--variant-jobs 2-3`
  to overlap the short hooks with the 16:9 full render.

## Status

v1 plus os.10.vs, the post-v1 studio, are shipped, including the `vgm` preset
(grade + watermark overlay, issue #37), studio image editing / track deletion
(#38/#39), film color grading (`saturation`/`grade`, #20), beat-reactive `color_punch`
(#21), vibe-aware `auto` presets (#19), the web studio render jobs / A-B / drag-and-drop
builder (#23/#24), and autosave (#25). CI installs `.[dev,ui]`, generates fixtures, and
runs the full suite.

Open work is tracked in
[issues](https://github.com/austinatchley/visualizer-pipeline/issues):

- #11 advanced section/phrase detection
- #12 performance tuning
- #14 video snippets
- #29 frontend componentization (Lit)
- #29 frontend componentization (Lit)