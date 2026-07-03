"""Diagnostic plots for a model's scraped data and its price regression.
Uses matplotlib's non-interactive Agg backend so it works headless (saves PNG)."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .pricing import CURRENT_YEAR, PriceModel


def make(name: str, rows: list[dict], model: PriceModel, out_path: str) -> None:
    """rows: priced listings (dicts with year, mileage_km, price)."""
    ages = [CURRENT_YEAR - r["year"] for r in rows]
    kms = [r["mileage_km"] for r in rows]
    prices = [r["price"] for r in rows]
    years = [r["year"] for r in rows]
    preds = [model.predict(a, k) for a, k in zip(ages, kms)]
    resid = [p - q for p, q in zip(prices, preds)]

    fig, ax = plt.subplots(2, 2, figsize=(13, 10))
    fig.suptitle(f"{name} — {len(rows)} listings · {model.kind} model "
                 f"(n={model.n}, R²={model.r2:.2f})", fontsize=14, weight="bold")

    # 1) Price vs mileage, coloured by model year.
    sc = ax[0, 0].scatter(kms, prices, c=years, cmap="viridis", s=28, alpha=0.8)
    ax[0, 0].set(xlabel="mileage (km)", ylabel="price (€)",
                 title="Price vs mileage (colour = year)")
    fig.colorbar(sc, ax=ax[0, 0], label="year")

    # 2) Regression performance: predicted vs actual.
    ax[0, 1].scatter(preds, prices, s=28, alpha=0.7, color="#2563eb")
    lo, hi = min(prices + preds), max(prices + preds)
    ax[0, 1].plot([lo, hi], [lo, hi], "r--", lw=1, label="perfect fit (y=x)")
    ax[0, 1].set(xlabel="predicted €", ylabel="actual €",
                 title="Regression performance")
    ax[0, 1].legend()

    # 3) Residuals vs predicted (should scatter around 0).
    ax[1, 0].axhline(0, color="grey", lw=1)
    ax[1, 0].scatter(preds, resid, s=28, alpha=0.7, color="#1f9d55")
    ax[1, 0].set(xlabel="predicted €", ylabel="actual − predicted €",
                 title="Residuals (below the line = underpriced = candidate deals)")

    # 4) Year vs mileage — spot year/km mismatches (points hugging the diagonal
    #    where km ≈ year, or implausible combos).
    ax[1, 1].scatter(years, kms, s=28, alpha=0.7, color="#b45309")
    ax[1, 1].set(xlabel="year", ylabel="mileage (km)",
                 title="Year vs mileage (outliers = likely misparse)")

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
