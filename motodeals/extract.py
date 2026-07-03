"""Local-LLM structured extraction from Spanish motorbike descriptions.

Runs 100% locally against an Ollama server (default http://localhost:11434) —
no API keys, no cloud, $0. We pass a JSON Schema as Ollama's `format`, which
*constrains generation* so the model can only emit JSON matching our shape.
Combined with temperature 0 this is deterministic and always parseable.

Recommended models (pull with `ollama pull ...`):
    GPU (GTX 1660S, 6GB):  qwen2.5:7b   (Q4_K_M, ~4.7GB — default instruct build)
    CPU automation:        qwen2.5:3b   (~2GB)
Qwen2.5 is chosen for strong Spanish + reliable JSON/instruction following.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"

# JSON Schema Ollama uses to constrain the model's output.
SCHEMA = {
    "type": "object",
    "properties": {
        # null when the description never states mileage (better than guessing).
        "mileage": {"type": ["integer", "null"]},
        "condition": {"type": "string", "enum": ["new", "used", "needs_work"]},
        # null = not mentioned; we don't assume absence means "no ITV".
        "has_itv": {"type": ["boolean", "null"]},
        "red_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["mileage", "condition", "has_itv", "red_flags"],
}

SYSTEM_PROMPT = """\
You extract structured data from second-hand motorbike ads written in Spanish.
The input text may contain SEO keyword spam (unrelated bike names) — ignore it.
Return ONLY a JSON object with exactly these fields:

- mileage: odometer in kilometres as an integer. Normalise Spanish notation:
  "120.000 km" -> 120000, "120k" -> 120000, "45 mil km" -> 45000. If mileage is
  not stated anywhere, use null. Never invent a number.
- condition: exactly one of:
    "new"        -> brand new / a estrenar / 0 km.
    "used"       -> normal used bike in working order.
    "needs_work" -> needs repairs / no arranca / para piezas / averiada / accidentada.
- has_itv: true if the ad says the ITV (Spanish roadworthiness inspection) is
  valid/passed ("ITV al día", "ITV en vigor", "ITV pasada"); false if it says the
  ITV is expired/failed; null if the ITV is not mentioned.
- red_flags: an array of SHORT English phrases naming concrete problems mentioned:
  e.g. "rust", "engine noise", "crash damage", "oil leak", "missing documents",
  "electrical fault", "clutch worn", "needs battery". Empty array if none.

Write all keys and all red_flags text in ENGLISH, even though the input is Spanish.
Output the JSON object and nothing else."""

# One few-shot example steadies formatting and the Spanish->English mapping.
EXAMPLE_USER = (
    "Yamaha MT-07 A2. Tiene 45.000 km, ITV al día. Todo funciona pero hace un "
    "ruido raro en el motor en frío y tiene algo de óxido en el escape. mt09 mt03 kawasaki"
)
EXAMPLE_ASSISTANT = json.dumps({
    "mileage": 45000,
    "condition": "used",
    "has_itv": True,
    "red_flags": ["engine noise when cold", "exhaust rust"],
}, ensure_ascii=False)


class ExtractionError(RuntimeError):
    pass


def extract(text: str, *, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST,
            timeout: int = 120) -> dict:
    """Extract the structured fields from one ad's title+description."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": EXAMPLE_USER},
            {"role": "assistant", "content": EXAMPLE_ASSISTANT},
            {"role": "user", "content": text.strip()[:6000]},
        ],
        "format": SCHEMA,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 2048},
    }
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except urllib.error.URLError as e:
        raise ExtractionError(
            f"Could not reach Ollama at {host} ({e}). Is `ollama serve` running "
            f"and `{model}` pulled?"
        ) from e

    content = payload.get("message", {}).get("content", "")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:  # should not happen with schema-constrained output
        raise ExtractionError(f"Model returned non-JSON: {content!r}") from e
    return _validate(data)


def _validate(data: dict) -> dict:
    """Coerce/repair the model output to our contract (belt-and-braces even
    though the schema already constrains it)."""
    mileage = data.get("mileage")
    if isinstance(mileage, float):
        mileage = int(mileage)
    if not isinstance(mileage, int):
        mileage = None

    condition = data.get("condition")
    if condition not in ("new", "used", "needs_work"):
        condition = "used"

    has_itv = data.get("has_itv")
    if has_itv not in (True, False, None):
        has_itv = None

    flags = data.get("red_flags") or []
    flags = [str(f).strip() for f in flags if str(f).strip()]

    return {"mileage": mileage, "condition": condition,
            "has_itv": has_itv, "red_flags": flags}
