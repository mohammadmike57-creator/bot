"""Triple-barrier labelling for supervised training.

Labels necessarily look into the future (they are the *target*). They are
never used as features, and the final ``horizon`` rows - whose outcome is not
yet known - are dropped. Walk-forward splits embargo ``horizon`` bars between
train and test so label windows never overlap the test period.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HOLD, BUY, SELL = 0, 1, 2
CLASS_NAMES = {HOLD: "HOLD", BUY: "BUY", SELL: "SELL"}


def triple_barrier_labels(features: pd.DataFrame, barrier_atr: float = 1.5, horizon: int = 24) -> pd.Series:
    """BUY if +barrier is touched before -barrier within ``horizon`` bars,
    SELL for the mirror case, HOLD if neither (or both in the same bar)."""
    close = features["close"].to_numpy()
    high = features["high"].to_numpy()
    low = features["low"].to_numpy()
    atr = features["atr"].to_numpy()
    n = len(features)
    labels = np.full(n, np.nan)
    for i in range(n - horizon):
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        up = close[i] + barrier_atr * atr[i]
        dn = close[i] - barrier_atr * atr[i]
        label = HOLD
        for j in range(i + 1, i + horizon + 1):
            hit_up = high[j] >= up
            hit_dn = low[j] <= dn
            if hit_up and hit_dn:
                label = HOLD  # ambiguous inside one bar
                break
            if hit_up:
                label = BUY
                break
            if hit_dn:
                label = SELL
                break
        labels[i] = label
    return pd.Series(labels, index=features.index, name="label")
