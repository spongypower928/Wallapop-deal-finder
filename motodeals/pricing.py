"""Per-model fair-price estimation.

We fit a tiny linear model  price ≈ b0 + b1·age + b2·(mileage/1000)  by ordinary
least squares, implemented in pure Python (no numpy/sklearn — keeps us off heavy
binary wheels and safe on Python 3.14). A "deal" is a listing priced well below
what this model predicts for its age and mileage.

With very little data (or no usable features) we fall back to a constant model
(the mean price), so `deals` still works on day one and sharpens as data grows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

CURRENT_YEAR = date.today().year
MIN_SAMPLES_FOR_REGRESSION = 6   # linear (age + km)
MIN_SAMPLES_FOR_QUAD = 12        # quadratic (age + km + km²) needs a bit more data
PRICE_FLOOR = 300.0              # predictions never go below this


@dataclass
class PriceModel:
    coef: list[float]          # [intercept, per-year-age, per-1000km, per-(1000km)²]
    kind: str                  # 'quadratic' | 'linear' | 'mean'
    n: int                     # training samples used
    r2: float                  # in-sample fit quality (0..1); 0 for mean model

    def predict(self, age: float, mileage_km: float) -> float:
        b0, b_age, b_km, b_km2 = self.coef
        kmk = mileage_km / 1000.0
        # Guard: if the parabola opens upward it would (wrongly) predict price
        # RISING past a certain mileage. Clamp mileage at that vertex so high-km
        # bikes plateau at a floor instead of curving back up.
        if b_km2 > 0:
            vertex = -b_km / (2 * b_km2)
            if vertex > 0 and kmk > vertex:
                kmk = vertex
        val = b0 + b_age * age + b_km * kmk + b_km2 * kmk * kmk
        return max(val, PRICE_FLOOR)


def _solve(a: list[list[float]], y: list[float]) -> list[float] | None:
    """Solve a·x = y for small square systems via Gaussian elimination with
    partial pivoting. Returns None if the system is (near-)singular."""
    n = len(a)
    m = [row[:] + [y[i]] for i, row in enumerate(a)]  # augmented
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-9:
            return None
        m[col], m[piv] = m[piv], m[col]
        pivval = m[col][col]
        m[col] = [v / pivval for v in m[col]]
        for r in range(n):
            if r != col and m[r][col]:
                factor = m[r][col]
                m[r] = [v - factor * m[col][i] for i, v in enumerate(m[r])]
    return [m[i][n] for i in range(n)]


def fit(samples: list[tuple[float, float, float]]) -> PriceModel:
    """samples: list of (age_years, mileage_km, price). Deduplicated by the
    caller. Prefers a quadratic-in-mileage fit (so high km is punished
    progressively), falling back to linear, then to the mean price."""
    prices = [s[2] for s in samples]
    n = len(samples)
    mean_price = sum(prices) / n if n else 0.0

    # (min_samples, kind, n_features) — most expressive first.
    for min_n, kind, ncols in ((MIN_SAMPLES_FOR_QUAD, "quadratic", 4),
                               (MIN_SAMPLES_FOR_REGRESSION, "linear", 3)):
        if n < min_n:
            continue
        # Design rows: [1, age, km/1000, (km/1000)²] (drop last col if linear).
        X = []
        for s in samples:
            kmk = s[1] / 1000.0
            X.append([1.0, s[0], kmk, kmk * kmk][:ncols])
        xtx = [[sum(X[k][i] * X[k][j] for k in range(n)) for j in range(ncols)]
               for i in range(ncols)]
        xty = [sum(X[k][i] * prices[k] for k in range(n)) for i in range(ncols)]
        coef = _solve(xtx, xty)
        if coef is None:
            continue
        model = PriceModel(coef=coef + [0.0] * (4 - ncols), kind=kind, n=n, r2=0.0)
        preds = [model.predict(s[0], s[1]) for s in samples]  # includes clamp+floor
        ss_res = sum((p - q) ** 2 for p, q in zip(prices, preds))
        ss_tot = sum((p - mean_price) ** 2 for p in prices) or 1.0
        model.r2 = max(0.0, 1.0 - ss_res / ss_tot)
        return model

    return PriceModel(coef=[mean_price, 0.0, 0.0, 0.0], kind="mean", n=n, r2=0.0)


def dedup_samples(rows: list[dict]) -> list[tuple[float, float, float]]:
    """Collapse near-identical listings (same dealer reposting the same bike)
    so they don't over-weight the fit. Key = (year, mileage, rounded price)."""
    seen: dict[tuple, tuple[float, float, float]] = {}
    for r in rows:
        key = (r["year"], r["mileage_km"], round(r["price"] / 50) * 50)
        age = CURRENT_YEAR - r["year"]
        seen[key] = (age, r["mileage_km"], r["price"])
    return list(seen.values())
