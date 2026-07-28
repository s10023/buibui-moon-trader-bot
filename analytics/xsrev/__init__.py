"""P3 cross-sectional short-horizon reversal sleeve (read-only, default-off).

A sign-flipped short-horizon (2-7 day) cross-sectional return signal, fed through
the validated XS-momentum demean/leverage/cost/governor book via its injectable
`forecasts=` hook. Candidate second strong edge, decorrelated from XS-solo. Pure,
read-only over ``analytics.db``, additive — no schema/golden change.
"""

from analytics.xsrev.config import ReversalConfig

__all__ = ["ReversalConfig"]
