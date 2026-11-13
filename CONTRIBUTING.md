# Contributing

Every change starts with an issue. This keeps scope explicit and the history readable.

## Workflow

1. **Issue first.** Find an existing issue or open one using the templates. Agree on the
   scope and acceptance criteria before writing code.
2. **Branch.** For anything touching rendering, planning, or config, branch from `main`:
   `git switch -c <issue-number>-<short-slug>` (e.g. `9-ci`).
3. **Commit.** Imperative subject (≤72 chars), explain *why* in the body, and reference
   the issue:
   - `Refs #12` for work in progress
   - `Closes #12` on the final commit
4. **Pull request.** PRs must link the issue (`Closes #N`), pass CI, and complete the
   checklist in the PR template.
5. **Small, low-risk changes** (docs, typos) may be committed directly to `main` with
   `Closes #N`. When in doubt, open a PR.
6. **Keep `main` green.** Never push a failing test suite.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ui]"
python scripts/make_fixtures.py   # synthetic tracks for integration tests
```

Requirements: Python 3.12, `ffmpeg`/`ffprobe`, and a system font (or `style.title_font`).

## Before opening a PR

- [ ] `pytest -q` passes (55+ tests; integration tests render short fixtures).
- [ ] `vp inspect fixtures/d_short` and `vp render fixtures/d_short --aspect all`
      behave as expected.
- [ ] README / DESIGN updated when behavior or config changes.
- [ ] No secrets, generated media, or `out/` artifacts staged (`fixtures/`, `out/`,
      `.venv/` are gitignored).
- [ ] The commit closes or references an issue.

## Style

- Pure logic belongs in `plan.py`, `hud.py`, `config.py` and is unit-tested.
- Renderer changes should extend `tests/test_render_smoke.py` (ffprobe assertions).
- Design decisions and their rationale live in `DESIGN.md`; update it rather than letting
  behavior drift.