"""The two measurement primitives both ownership evaluators report.

``backtest.py`` scores leave-one-season-out and ``evaluate.py`` scores strict
walk-forward. Their protocols are different on purpose (``evaluate.py:3-7``
states why), but the metric underneath has to be the same number or the two
reports are not comparable and nobody can say which protocol moved.

Both modules carried their own copy of ``mae_pp`` with identical bodies and
docstrings that differed by one word. That is the only thing
ARCHITECTURE_REVIEW.md check 7 found worth sharing between them, and sharing it
is the whole content of this module: it is a leaf over numpy, and it must not
grow a protocol, a fold, or a loader.
"""

from __future__ import annotations

import numpy as np

#: Nominal coverages whose empirical attainment is reported. A point forecast
#: with an honest MAE and a dishonest width is unusable for a rank objective:
#: P(rank < threshold) depends on the spread of my score against the field, and
#: an overconfident ownership forecast makes every differential look safer than
#: it is.
COVERAGES = (0.5, 0.8, 0.95)


def mae_pp(truth: np.ndarray, pred: np.ndarray) -> float:
    """Mean absolute error in percentage points of ownership share."""
    return float(100.0 * np.mean(np.abs(np.asarray(truth) - np.asarray(pred))))
