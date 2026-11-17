"""Regenerate the studio's API types from the FastAPI schema (issue #63).

    python scripts/gen_api_types.py          # dump openapi.json, then run openapi-typescript
    python scripts/gen_api_types.py --json   # only dump openapi.json (no Node needed)

Writes `vp/ui/types/openapi.json` (from `create_app().openapi()`, i.e. the Pydantic
models in vp/ui/models.py) and `vp/ui/types/api.d.ts` (via `npm run gen:api`).
Both are committed. CI fails if either is stale: tests/test_ui.py compares the JSON
with a fresh dump, and the `typecheck` job regenerates `api.d.ts` and diffs it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPES_DIR = ROOT / "vp" / "ui" / "types"
OPENAPI_JSON = TYPES_DIR / "openapi.json"


def openapi_text() -> str:
    """The studio's OpenAPI document, serialized exactly as it is committed."""
    from vp.ui.server import create_app

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(root=Path(tmp) / "tracks", out=Path(tmp) / "out")
        return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="only write openapi.json")
    args = parser.parse_args(argv)

    OPENAPI_JSON.write_text(openapi_text())
    print(f"wrote {OPENAPI_JSON.relative_to(ROOT)}")
    if args.json:
        return 0
    return subprocess.run(["npm", "run", "--silent", "gen:api"], cwd=ROOT).returncode


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
