"""Brand + generation settings, loaded from a TOML file so the name can change freely."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class BrandConfig:
    # Branding (shown on demo PDFs) - nothing in the code hardcodes the name
    name: str = "Listwell"
    tagline: str = "Product descriptions that sell, in every language your customers speak."
    website: str = ""
    contact_email: str = ""
    company: str = "Metainsights BV"
    primary_color: str = "#1F4E79"
    accent_color: str = "#E07A1F"
    logo_path: str = ""

    # Generation
    model: str = "claude-opus-5-5"
    effort: str = "medium"  # low | medium | high
    use_fallbacks: bool = True  # server-side refusal fallback (beta)
    inference_geo: str = ""  # passthrough to the API's inference_geo when set
    languages: list[str] = field(default_factory=lambda: ["en", "de", "fr", "nl"])
    tone: str = "warm, clear and concrete; no hype, no exclamation marks"
    max_image_px: int = 1568
    concurrency: int = 4


def load_config(path: str | Path | None = None) -> BrandConfig:
    """Load brand.toml. Missing file or keys fall back to defaults."""
    cfg = BrandConfig()
    if path is None:
        default = Path("brand.toml")
        if not default.exists():
            return cfg
        path = default
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    flat: dict = {}
    for section in data.values() if all(isinstance(v, dict) for v in data.values()) else [data]:
        flat.update(section)
    known = {f.name for f in fields(BrandConfig)}
    unknown = set(flat) - known
    if unknown:
        raise ValueError(f"Unknown keys in {path}: {', '.join(sorted(unknown))}")
    for k, v in flat.items():
        setattr(cfg, k, v)
    return cfg
