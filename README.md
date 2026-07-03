# Wallapop deal finder (`motodeals`)

Find underpriced used motorbikes on Spanish marketplaces. Scrapes listings for a
few specific models, uses a **local LLM** to read the (Spanish, keyword-spammed)
descriptions into structured fields, fits a per-model fair-price model, and ranks
listings by how far below market they are — so you only go check the good ones.

**100% local, $0 — no cloud APIs, no keys.**

## Pipeline

```
scrape  ──▶  extract  ──▶  deals
(browser)    (local LLM)    (price model + ranking)
```

| Stage | How |
|-------|-----|
| **scrape** | Playwright drives Wallapop's own search page and intercepts the JSON its front-end fetches (sidesteps the signed-request 403). Broad multi-keyword search for recall. |
| **extract** | [Ollama](https://ollama.com) + Qwen2.5, JSON-schema-constrained (always valid JSON) at temperature 0. Pulls `mileage`, `condition`, `has_itv`, `red_flags` from free text. |
| **deals** | Title-based relevance filter + pure-Python OLS (`price ≈ b0 + b1·age + b2·km`). Ranks by % under the predicted fair price. |

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Local model runtime (see scripts/serve_ollama.sh for a no-root install)
ollama pull qwen2.5:7b      # GPU (6GB+); use qwen2.5:3b on CPU
```

Configure the bikes you track in [`config.toml`](config.toml).

## Usage

```bash
./scripts/serve_ollama.sh        # terminal 1: local model server
python -m motodeals scrape       # collect listings
python -m motodeals extract      # LLM structured extraction
python -m motodeals deals        # ranked deals (--all to see everything)
python -m motodeals list         # dump the database
```

## Notes

- Scraped data (`data/`) contains third-party sellers' personal data and is
  git-ignored — do not publish it.
- Respect each site's Terms of Service; this is for personal, low-volume use.

- **USE UNDER YOUR OWN RISK** 
