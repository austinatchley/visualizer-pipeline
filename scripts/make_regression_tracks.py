"""Synthesize arranged, genre-shaped beats with known BPMs (issue #54).

The basic fixtures (make_fixtures.py) are a click-track groove; librosa reads
the 140 BPM one as 92.3. These tracks are closer to real material: layered
drums with velocity variation and swing, a bass line, chord pads, and an
arrangement (intro / verse / drop / breakdown / drop / outro) whose energy
changes the analysis and planner should pick up.

Everything is deterministic (seeded) and generated in a few seconds, so the
regression tests in tests/test_regression_audio.py synthesize them on the fly.
Run this script to write them as track folders for manual `vp` runs:

    python scripts/make_regression_tracks.py [OUT_DIR]   # default: fixtures/regression
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100

# Section layers: which parts play, and how hard.
LAYERS = {
    "intro": {"pad": 0.8, "hats": 0.5},
    "verse": {"kick": 0.8, "snare": 0.8, "hats": 0.7, "bass": 0.6, "pad": 0.5},
    "drop": {
        "kick": 1.0,
        "snare": 1.0,
        "hats": 1.0,
        "openhat": 1.0,
        "bass": 1.0,
        "pad": 0.7,
        "stab": 1.0,
    },
    "breakdown": {"pad": 1.0, "hats": 0.3},
    "outro": {"kick": 0.6, "pad": 0.6},
}
HIGH_ENERGY = {"drop"}


@dataclass(frozen=True)
class Spec:
    name: str
    bpm: float
    # (section, bars) in order; 4/4 throughout.
    arrangement: tuple[tuple[str, int], ...]
    # Drum pattern on a 16-step bar: kick/snare steps; hats every `hat_step` steps.
    kick: tuple[int, ...]
    snare: tuple[int, ...]
    hat_step: int = 2
    swing: float = 0.0  # fraction of a 16th that off-16ths are delayed
    seed: int = 0

    @property
    def bar_seconds(self) -> float:
        return 4 * 60.0 / self.bpm

    def sections(self) -> list[tuple[str, float, float]]:
        """(name, start_s, end_s) per section."""
        out, t = [], 0.0
        for name, bars in self.arrangement:
            out.append((name, t, t + bars * self.bar_seconds))
            t += bars * self.bar_seconds
        return out

    @property
    def duration(self) -> float:
        return self.sections()[-1][2]


ARRANGEMENT = (("intro", 8), ("verse", 8), ("drop", 8), ("breakdown", 4), ("drop", 8), ("outro", 4))

SPECS = [
    Spec(
        "boombap_90",
        90.0,
        ARRANGEMENT,
        kick=(0, 7, 10),
        snare=(4, 12),
        hat_step=2,
        swing=0.3,
        seed=1,
    ),
    Spec("house_124", 124.0, ARRANGEMENT, kick=(0, 4, 8, 12), snare=(4, 12), hat_step=2, seed=2),
    # Half-time trap: snare on beat 3, so 70 BPM is an acceptable reading.
    Spec("trap_140", 140.0, ARRANGEMENT, kick=(0, 3, 10), snare=(8,), hat_step=1, seed=3),
    Spec(
        "lofi_75", 75.0, ARRANGEMENT, kick=(0, 6, 9), snare=(4, 12), hat_step=2, swing=0.4, seed=4
    ),
]


# --- voices -------------------------------------------------------------------


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR)) / SR


def _highpass(x: np.ndarray, amount: int = 1) -> np.ndarray:
    for _ in range(amount):
        x = np.diff(x, prepend=0.0)
    return x


def _kick() -> np.ndarray:
    t = _t(0.35)
    freq = 45 + 110 * np.exp(-t * 35)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 9)
    click = np.exp(-t * 600) * 0.6
    return body + click


def _snare(rng: np.random.Generator) -> np.ndarray:
    t = _t(0.25)
    tone = 0.5 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25)
    noise = _highpass(rng.normal(0, 1, len(t))) * 0.5 * np.exp(-t * 18)
    return tone + noise


def _hat(rng: np.random.Generator, length: float) -> np.ndarray:
    t = _t(length)
    return _highpass(rng.normal(0, 1, len(t)), 2) * 0.25 * np.exp(-t * (6 / length))


def _note(freq: float, seconds: float, attack: float, decay: float, harmonics: int) -> np.ndarray:
    t = _t(seconds)
    env = np.minimum(1.0, t / attack) * np.exp(-t * decay)
    wave = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, harmonics + 1))
    return env * wave


def _add(out: np.ndarray, at: float, sound: np.ndarray, gain: float) -> None:
    start = int(at * SR)
    if start >= len(out):
        return
    end = min(len(out), start + len(sound))
    out[start:end] += gain * sound[: end - start]


# Am - F - C - G, one chord per bar (root in A1..G2 for bass).
PROGRESSION = [(55.0, (220.0, 261.63, 329.63)), (43.65, (174.61, 220.0, 261.63)),
               (65.41, (261.63, 329.63, 392.0)), (49.0, (196.0, 246.94, 293.66))]  # fmt: skip


def synthesize(spec: Spec) -> np.ndarray:
    """Render `spec` to a mono float32 signal at SR."""
    rng = np.random.default_rng(spec.seed)
    out = np.zeros(int(spec.duration * SR) + SR)
    step = spec.bar_seconds / 16
    kick, snare = _kick(), _snare(rng)
    bar_index = 0
    for name, start, end in spec.sections():
        layers = LAYERS[name]
        for bar_start in np.arange(start, end - 1e-6, spec.bar_seconds):
            root, chord = PROGRESSION[bar_index % len(PROGRESSION)]
            bar_index += 1
            for s in range(16):
                at = bar_start + s * step + (spec.swing * step if s % 2 else 0.0)
                vel = 0.85 + 0.15 * rng.random()
                if "kick" in layers and s in spec.kick:
                    _add(out, at, kick, layers["kick"] * vel)
                if "snare" in layers and s in spec.snare:
                    _add(out, at, snare, 0.8 * layers["snare"] * vel)
                if "hats" in layers and s % spec.hat_step == 0:
                    accent = 1.0 if s % 4 == 0 else 0.6
                    _add(out, at, _hat(rng, 0.05), layers["hats"] * accent * vel)
                if "openhat" in layers and s % 4 == 2:
                    _add(out, at, _hat(rng, 0.25), 0.7 * layers["openhat"] * vel)
                if "bass" in layers and s in spec.kick:
                    _add(out, at, _note(root, step * 3, 0.005, 6, 3), 0.5 * layers["bass"])
                if "stab" in layers and s in (0, 6, 10):
                    for f in chord:
                        _add(out, at, _note(f, step * 1.5, 0.003, 14, 4), 0.07 * layers["stab"])
            if "pad" in layers:
                for f in chord:
                    _add(
                        out,
                        bar_start,
                        _note(f / 2, spec.bar_seconds, 0.3, 0.6, 2),
                        0.06 * layers["pad"],
                    )

    out = out[: int(spec.duration * SR)]
    return (out / (np.max(np.abs(out)) or 1.0) * 0.89).astype(np.float32)


def write_track(spec: Spec, folder: Path, image: bool = True) -> Path:
    """Write `spec` as a track folder: audio.wav, track.json, and one image."""
    folder.mkdir(parents=True, exist_ok=True)
    audio = folder / "audio.wav"
    sf.write(audio, synthesize(spec), SR, subtype="PCM_16")
    manifest = {
        "title": spec.name.replace("_", " ").title(),
        "regression": {
            "bpm": spec.bpm,
            "sections": [[n, round(s, 3), round(e, 3)] for n, s, e in spec.sections()],
        },
    }
    (folder / "track.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if image:
        from PIL import Image

        (folder / "images").mkdir(exist_ok=True)
        shade = 40 + 40 * spec.seed
        Image.new("RGB", (1080, 1080), (shade, 30, 90)).save(folder / "images" / "01.png")
    return audio


def main(argv: list[str]) -> int:
    out = (
        Path(argv[0])
        if argv
        else Path(__file__).resolve().parent.parent / "fixtures" / "regression"
    )
    for spec in SPECS:
        write_track(spec, out / spec.name)
    print(f"wrote {len(SPECS)} regression tracks to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
