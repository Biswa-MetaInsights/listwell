"""Turn a ProductInput into multilingual shop copy with Claude."""

from __future__ import annotations

import base64
import io
import json
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable

import anthropic
from PIL import Image

from .config import BrandConfig
from .models import LANGUAGE_NAMES, GeneratedCopy, LocalizedCopy, ProductInput, ProductResult

SYSTEM_PROMPT = """You are a senior e-commerce copywriter for small European web shops.
You write product copy that is native in each language: written fresh by someone who
lives there, never a literal translation of the English. Use the conventions a local
shopper expects (German: Sie-form unless the shop's tone says otherwise, metric units,
decimal comma; French: proper spacing before : ; ! ?; Dutch: je/jij-form for consumer shops).

Rules you never break:
- Only state what the facts or the photo support. Never invent dimensions, materials,
  certifications, origin, awards or test results. If something useful is missing,
  write around it and add a review note.
- No health, medical, environmental ("eco", "sustainable", "climate neutral") or
  "best/No. 1" claims unless the facts prove them. These are legally risky in the EU
  (UCPD, Green Claims rules, German UWG). Add a review note if the shop's facts make such a claim.
- Product copy is about the product only. Never mention returns, delivery, payment,
  warranty, legal rights, store counts or other shop-wide policies, even if the shop's
  text does. Advertising statutory rights (like the EU 14-day withdrawal) as a benefit
  is misleading under EU law.
- No exclamation marks, no emoji, no clichés like "elevate", "must-have", "game-changer".
- description_html uses only <p>, <ul>, <li>, <strong>. 80-180 words. Lead with the
  benefit for the buyer, then the details, then care/use info if known.
- title max 70 characters: product type, name, and one distinguishing detail.
- seo_title max 60 characters, meta_description max 155 characters, both containing
  the main search term a shopper in that country would type.
- Tags are lowercase search phrases people in that country actually use.
"""


def _image_block(ref: str, max_px: int) -> dict:
    if ref.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": ref}}
    path = Path(ref)
    img = Image.open(path)
    img.thumbnail((max_px, max_px))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _output_schema(languages: list[str]) -> dict:
    s = {"type": "string"}
    arr = {"type": "array", "items": {"type": "string"}}
    copy_schema = {
        "type": "object",
        "properties": {
            "language": {"type": "string", "enum": languages},
            "title": s,
            "short_description": s,
            "description_html": s,
            "bullet_points": arr,
            "seo_title": s,
            "meta_description": s,
            "tags": arr,
        },
        "required": list(LocalizedCopy.model_fields),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"copies": {"type": "array", "items": copy_schema}, "review_notes": arr},
        "required": ["copies", "review_notes"],
        "additionalProperties": False,
    }


def build_user_content(product: ProductInput, cfg: BrandConfig, languages: list[str]) -> list[dict]:
    content: list[dict] = [_image_block(ref, cfg.max_image_px) for ref in product.images[:3]]
    lines = [f"Product name: {product.name}"]
    for label, value in [("Brand", product.brand), ("Category", product.category),
                         ("Price", product.price), ("SKU", product.sku)]:
        if value:
            lines.append(f"{label}: {value}")
    if product.facts:
        lines.append("Facts from the shop:\n" + "\n".join(f"- {f}" for f in product.facts))
    if product.original_description:
        lines.append("The shop's current description (use for facts only, do not copy its style):\n"
                     + product.original_description[:3000])
    langs = ", ".join(f"{LANGUAGE_NAMES.get(l, l)} ({l})" for l in languages)
    lines.append(f"\nShop tone: {cfg.tone}")
    lines.append(f"Write one complete copy object per language, in this order: {langs}.")
    if not product.images:
        lines.append("No photo was provided; rely on the facts only.")
    content.append({"type": "text", "text": "\n".join(lines)})
    return content


class GenerationError(RuntimeError):
    pass


class Generator:
    def __init__(self, cfg: BrandConfig, client: anthropic.Anthropic | None = None):
        self.cfg = cfg
        self.client = client or anthropic.Anthropic()

    def generate(self, product: ProductInput, languages: list[str] | None = None) -> ProductResult:
        languages = languages or self.cfg.languages
        kwargs: dict = dict(
            model=self.cfg.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_content(product, self.cfg, languages)}],
            output_config={
                "effort": self.cfg.effort,
                "format": {"type": "json_schema", "schema": _output_schema(languages)},
            },
        )
        if self.cfg.inference_geo:
            kwargs["inference_geo"] = self.cfg.inference_geo
        if self.cfg.use_fallbacks:
            resp = self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            resp = self.client.messages.create(**kwargs)

        if resp.stop_reason == "refusal":
            raise GenerationError(f"{product.name}: model declined ({resp.stop_details})")
        if resp.stop_reason == "max_tokens":
            raise GenerationError(f"{product.name}: output cut off at max_tokens")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise GenerationError(f"{product.name}: no text in response")
        copy = GeneratedCopy.model_validate(json.loads(text))
        missing = set(languages) - {c.language for c in copy.copies}
        if missing:
            raise GenerationError(f"{product.name}: missing languages {sorted(missing)}")
        return ProductResult(product=product, copy=copy, model=resp.model,
                             input_tokens=resp.usage.input_tokens,
                             output_tokens=resp.usage.output_tokens)

    def generate_many(
        self,
        products: Iterable[ProductInput],
        languages: list[str] | None = None,
        on_done: Callable[[ProductInput, ProductResult | None, Exception | None], None] | None = None,
    ) -> tuple[list[ProductResult], list[tuple[ProductInput, Exception]]]:
        products = list(products)
        results: dict[int, ProductResult] = {}
        failures: list[tuple[ProductInput, Exception]] = []

        def work(i_p):
            i, p = i_p
            try:
                r = self.generate(p, languages)
                results[i] = r
                if on_done:
                    on_done(p, r, None)
            except Exception as e:  # keep the batch going, report at the end
                failures.append((p, e))
                if on_done:
                    on_done(p, None, e)

        with ThreadPoolExecutor(max_workers=max(1, self.cfg.concurrency)) as pool:
            list(pool.map(work, enumerate(products)))
        return [results[i] for i in sorted(results)], failures


class FakeGenerator(Generator):
    """Offline stand-in for tests and dry runs: no API calls, deterministic output."""

    def __init__(self, cfg: BrandConfig):
        self.cfg = cfg

    def generate(self, product: ProductInput, languages: list[str] | None = None) -> ProductResult:
        languages = languages or self.cfg.languages
        facts = product.facts or ["(no facts given)"]
        copies = [
            LocalizedCopy(
                language=l,
                title=f"[{l}] {product.name}",
                short_description=f"[{l}] {product.name}: {facts[0]}.",
                description_html="<p>" + f"[{l}] {product.name}." + "</p><ul>"
                + "".join(f"<li>{f}</li>" for f in facts) + "</ul>",
                bullet_points=facts[:5],
                seo_title=f"{product.name}"[:60],
                meta_description=f"[{l}] {product.name} - {', '.join(facts)}"[:155],
                tags=[product.category.lower()] if product.category else ["product"],
            )
            for l in languages
        ]
        return ProductResult(product=product, copy=GeneratedCopy(copies=copies, review_notes=["dry run"]),
                             model="dry-run")
