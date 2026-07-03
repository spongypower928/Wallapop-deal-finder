"""Load the TOML config into simple dataclasses."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


def _normalize(s: str) -> str:
    """Lowercase and strip non-alphanumerics so 'MT-07' == 'MT 07' == 'mt07'."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class Location:
    latitude: float
    longitude: float
    distance_km: int = 400


@dataclass
class Search:
    name: str
    # One or more broad search terms. We run each and merge results to maximise
    # recall (Wallapop search is fuzzy and sellers keyword-stuff descriptions,
    # so casting a wide net + de-duplicating beats a single narrow query).
    keywords: list[str]
    min_price: int | None = None
    max_price: int | None = None
    # Substrings the listing title must all contain (spacing/punctuation ignored)
    # to count as this model. None = keep everything.
    title_contains: list[str] | None = None
    # Substrings that, if present in the title, EXCLUDE the listing (e.g. other
    # models that borrow the name for SEO: "tracer", "tenere").
    title_excludes: list[str] | None = None

    def __post_init__(self):
        if isinstance(self.keywords, str):
            self.keywords = [self.keywords]

    def matches_title(self, title: str | None) -> bool:
        norm = _normalize(title or "")
        if self.title_excludes and any(_normalize(t) in norm for t in self.title_excludes):
            return False
        if self.title_contains:
            return all(_normalize(tok) in norm for tok in self.title_contains)
        return True


@dataclass
class Config:
    location: Location
    searches: list[Search]


def load(path: Path = CONFIG_PATH) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    location = Location(**raw["location"])
    searches = [Search(**s) for s in raw.get("searches", [])]
    if not searches:
        raise ValueError(f"No [[searches]] defined in {path}")
    return Config(location=location, searches=searches)
