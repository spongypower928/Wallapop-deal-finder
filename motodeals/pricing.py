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
MIN_SAMPLES_FOR_REGRESSION = 6
PRICE_FLOOR = 300.0              # predictions never go below this


@dataclass
class PriceModel:
    coef: list[float]          # [intercept, per-year-age, per-1000km]
    kind: str                  # 'linear' | 'mean'
    n: int                     # training samples used
    r2: float                  # R² fit quality (0..1); 0 for mean model
    mae: float                 # median absolute error (€) — honest metric for truncated price bands

    def predict(self, age: float, mileage_km: float) -> float:
        b0, b_age, b_km = self.coef
        val = b0 + b_age * age + b_km * (mileage_km / 1000.0)
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
    caller. Fits a linear model (price ~ age + km), or falls back to mean."""
    prices = [s[2] for s in samples]
    n = len(samples)
    mean_price = sum(prices) / n if n else 0.0

    if n >= MIN_SAMPLES_FOR_REGRESSION:
        # Design matrix: [1, age, km/1000]
        X = [[1.0, s[0], s[1] / 1000.0] for s in samples]
        xtx = [[sum(X[k][i] * X[k][j] for k in range(n)) for j in range(3)]
               for i in range(3)]
        xty = [sum(X[k][i] * prices[k] for k in range(n)) for i in range(3)]
        coef = _solve(xtx, xty)
        if coef is not None:
            preds = [coef[0] + coef[1] * s[0] + coef[2] * (s[1] / 1000.0)
                     for s in samples]
            preds = [max(p, PRICE_FLOOR) for p in preds]  # clamp to floor
            ss_res = sum((p - q) ** 2 for p, q in zip(prices, preds))
            ss_tot = sum((p - mean_price) ** 2 for p in prices) or 1.0
            r2 = max(0.0, 1.0 - ss_res / ss_tot)
            # MAE: median absolute error (more robust than RMSE on truncated data)
            errs = sorted([abs(p - q) for p, q in zip(prices, preds)])
            mae = errs[len(errs) // 2] if errs else 0.0
            model = PriceModel(coef=coef, kind="linear", n=n, r2=r2, mae=mae)
            return model

    model = PriceModel(coef=[mean_price, 0.0, 0.0], kind="mean", n=n, r2=0.0, mae=0.0)
    return model


def dedup_samples(rows: list[dict]) -> list[tuple[float, float, float]]:
    """Collapse near-identical listings (same dealer reposting the same bike)
    so they don't over-weight the fit. Key = (year, mileage, rounded price)."""
    seen: dict[tuple, tuple[float, float, float]] = {}
    for r in rows:
        key = (r["year"], r["mileage_km"], round(r["price"] / 50) * 50)
        age = CURRENT_YEAR - r["year"]
        seen[key] = (age, r["mileage_km"], r["price"])
    return list(seen.values())
