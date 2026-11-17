"""Write generated copy in formats shops can import directly.

Before a client's first import, export one product from their shop and compare
the column headers: Shopify and WooCommerce both accept extra/missing columns,
but handles and IDs must match their existing products to update instead of duplicate.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import ProductResult


def _write(path: Path, header: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel opens umlauts and accents correctly
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


# ---------------------------------------------------------------- Shopify

SHOPIFY_HEADER = ["Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
                  "Variant SKU", "Variant Price", "Image Src", "SEO Title", "SEO Description"]


def shopify_products_csv(results: list[ProductResult], lang: str, path: Path) -> Path:
    """Shopify product import CSV in the shop's primary language.

    Import with "Overwrite products with matching handles" to update existing products.
    """
    rows = []
    for r in results:
        c = r.for_language(lang)
        if not c:
            continue
        p = r.product
        image = next((i for i in p.images if i.startswith("http")), "")
        rows.append([p.handle, c.title, c.description_html, p.brand, p.category,
                     ", ".join(c.tags), "TRUE", p.sku, p.price.split(" ")[0] if p.price else "",
                     image, c.seo_title, c.meta_description])
    return _write(path, SHOPIFY_HEADER, rows)


SHOPIFY_TRANSLATION_HEADER = ["Type", "Identification", "Field", "Locale", "Market", "Status",
                              "Default content", "Translated content"]


def shopify_translations_csv(results: list[ProductResult], primary: str, langs: list[str], path: Path) -> Path:
    """Translations for Shopify's 'Translate & Adapt' CSV import (Settings > Languages > Import).

    Identification is the Shopify product id when we know it (scraped from the shop),
    otherwise the handle; fill real ids from the shop's own translation export if needed.
    """
    fields = [("title", "title"), ("body_html", "description_html"),
              ("meta_title", "seo_title"), ("meta_description", "meta_description")]
    rows = []
    for r in results:
        base = r.for_language(primary)
        if not base:
            continue
        ident = r.product.shop_id or r.product.handle
        for lang in langs:
            if lang == primary or not (c := r.for_language(lang)):
                continue
            for shop_field, attr in fields:
                rows.append(["PRODUCT", ident, shop_field, lang, "", "",
                             getattr(base, attr), getattr(c, attr)])
    return _write(path, SHOPIFY_TRANSLATION_HEADER, rows)


# ---------------------------------------------------------------- WooCommerce

WOO_HEADER = ["ID", "Type", "SKU", "Name", "Published", "Short description", "Description",
              "Tags", "Categories", "Images", "Regular price",
              "Meta: _yoast_wpseo_title", "Meta: _yoast_wpseo_metadesc"]


def woocommerce_csv(results: list[ProductResult], lang: str, path: Path) -> Path:
    """WooCommerce built-in importer (Products > Import). One file per language,
    for WPML/Polylang setups or separate country shops. SEO goes to Yoast meta fields.

    Tick "Update existing products" to match on ID/SKU.
    """
    rows = []
    for r in results:
        c = r.for_language(lang)
        if not c:
            continue
        p = r.product
        images = ", ".join(i for i in p.images if i.startswith("http"))
        price = p.price.split(" ")[0] if p.price else ""
        rows.append([p.shop_id, "simple", p.sku, c.title, "1", c.short_description,
                     c.description_html, ", ".join(c.tags), p.category, images, price,
                     c.seo_title, c.meta_description])
    return _write(path, WOO_HEADER, rows)


# ---------------------------------------------------------------- review / raw

def review_csv(results: list[ProductResult], langs: list[str], path: Path) -> Path:
    """One row per product and language, for the client (or you) to proofread."""
    header = ["Handle", "SKU", "Language", "Title", "Short description", "Description (HTML)",
              "Bullet points", "SEO title", "SEO title length", "Meta description",
              "Meta length", "Tags", "Review notes"]
    rows = []
    for r in results:
        notes = " | ".join(r.copy_.review_notes)
        for lang in langs:
            if c := r.for_language(lang):
                rows.append([r.product.handle, r.product.sku, lang, c.title, c.short_description,
                             c.description_html, " | ".join(c.bullet_points), c.seo_title,
                             len(c.seo_title), c.meta_description, len(c.meta_description),
                             ", ".join(c.tags), notes])
    return _write(path, header, rows)


def results_json(results: list[ProductResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([r.model_dump(by_alias=True) for r in results],
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_results_json(path: Path) -> list[ProductResult]:
    return [ProductResult.model_validate(d) for d in json.loads(Path(path).read_text(encoding="utf-8"))]


def export_all(results: list[ProductResult], langs: list[str], out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    primary = langs[0]
    files = [
        results_json(results, out_dir / "results.json"),
        review_csv(results, langs, out_dir / "review.csv"),
        shopify_products_csv(results, primary, out_dir / "shopify" / f"products_{primary}.csv"),
        shopify_translations_csv(results, primary, langs, out_dir / "shopify" / "translations.csv"),
    ]
    files += [woocommerce_csv(results, l, out_dir / "woocommerce" / f"products_{l}.csv") for l in langs]
    return files
