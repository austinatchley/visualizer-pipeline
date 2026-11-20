import pytest

import vp.render


@pytest.fixture(scope="module")
def small_exports():
    """Run real exports (audio, fades, CRF 18, faststart) at preview resolution.

    End-to-end tests check the export path and orchestration, not pixels at
    1080p; full-res sizes and commands are pinned by `test_render_graph.py`
    and the render-graph snapshots. Use via
    `pytestmark = pytest.mark.usefixtures("small_exports")`."""
    full = vp.render.output_size
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            vp.render, "output_size", lambda aspect, preview=False: full(aspect, preview=True)
        )
        yield
