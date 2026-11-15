"""Turn a boolean time series into contiguous windows."""
from __future__ import annotations

import numpy as np


def find_windows(mask: np.ndarray, step_s: float) -> list[tuple[int, int]]:
    """Index ranges [start, end) where ``mask`` is True.

    TODO(joint): edges are only accurate to one time step.  Refine start/end by
    interpolating where elevation crosses the threshold, and decide how to treat
    windows that touch the start/end of the analysis period.
    """
    padded = np.concatenate(([False], np.asarray(mask, dtype=bool), [False]))
    change = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(s), int(e)) for s, e in zip(change[::2], change[1::2])]
