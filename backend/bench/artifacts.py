"""The artifact scorecard. It lives in the runtime package, `studi0trace.imaging.quality`,
because Auto scores its candidates with it; the bench measures exactly what the
service measures. Everything there (the private helpers too) is re-exported
here so bench code and the scratch tools keep importing `bench.artifacts`."""
from __future__ import annotations

from studi0trace.imaging import quality as _quality

globals().update({k: v for k, v in vars(_quality).items() if not k.startswith("__")})

from studi0trace.imaging.quality import (  # noqa: E402,F401  (the public names, for readers and linters)
    ARTIFACT_KEYS,
    LOWER_IS_BETTER,
    artifact_index,
    geometry_card,
    holes,
    id_map,
    is_clean,
    parse,
    path_polylines,
    scorecard,
    visible_samples,
)
