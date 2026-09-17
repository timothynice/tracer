"""Optional stage for `gradients=False`: split smooth regions into flat bands.

The partition never breaks a smooth ramp (that is the point of Vexel), so with
gradient fills disabled a ramp would collapse to one flat colour. Users who
turn gradients off expect posterised bands instead; this quantises the Lab
colour of each non-flat region with a step of `detail` and relabels connected
components.
"""
from __future__ import annotations

import numpy as np
from skimage.measure import label as cc_label
from skimage.segmentation import relabel_sequential, watershed


def posterize_regions(labels: np.ndarray, features: np.ndarray, detail: float, min_region: int, grad: np.ndarray) -> np.ndarray:
    out = np.zeros_like(labels)
    next_id = 1
    step = max(detail, 1.0)
    for lab in np.unique(labels):
        if lab == 0:
            continue
        m = labels == lab
        f = features[m][:, :3]
        rms = float(np.sqrt(((f - f.mean(axis=0)) ** 2).sum(axis=1).mean()))
        if rms <= step / 2:
            out[m] = next_id
            next_id += 1
            continue
        q = np.floor(features[..., :3] / step).astype(np.int64)
        key = (q[..., 0] * 7919 + q[..., 1]) * 7907 + q[..., 2]
        key = np.where(m, key - key[m].min() + 1, 0)
        # connected components per quantised colour
        comps = np.zeros(labels.shape, np.int64)
        for k in np.unique(key[m]):
            cc = cc_label(key == k, connectivity=1)
            comps[cc > 0] = cc[cc > 0] + comps.max()
        out[m] = comps[m] + next_id - 1
        next_id = int(out.max()) + 1
    # absorb tiny bands along the gradient like the partition does
    sizes = np.bincount(out.ravel())
    small = sizes < min_region
    small[0] = False
    if small.any():
        markers = out.copy()
        markers[small[out]] = 0
        if markers.max() > 0:
            out = watershed(grad, markers)
    out, _, _ = relabel_sequential(out)
    return out.astype(np.int32)
