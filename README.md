# Listwell (working name)

Multilingual product descriptions for small web shops. Give it a product photo plus a few
facts (or a product page URL) and it writes shop-ready copy in English, German, French and
Dutch: title, teaser, HTML description, bullets, SEO title, meta description and tags.
It exports files that Shopify and WooCommerce import directly, and a before/after PDF for
sales demos.

The brand name, colours, contact details, model and languages all live in `brand.toml`,
so renaming the business means editing that one file.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...      # from console.anthropic.com
pytest -q                                # offline tests, no API calls
```

## The three things you'll do

**1. Prospect a shop (free sample for sales).** Point it at a Shopify shop or product pages.
It scrapes up to 5 products, rewrites them, and builds `sample/sample.pdf` with the shop's
current text next to the new version.

```bash
listwell sample https://prospect-shop.de --prospect "Prospect GmbH" --limit 5
listwell sample https://shop.example/products/a https://shop.example/products/b
```

**2. Deliver a paid batch.** The client fills the intake sheet (Google Sheet, exported to
CSV, see `examples/products.csv`). `facts` and `images` are `|`-separated; image paths are
relative to the CSV, or https URLs.

```bash
listwell generate client/products.csv --out client/out
```

Output in `client/out/`:

| File | What it is |
|---|---|
| `review.csv` | One row per product and language, with SEO lengths and review notes. Proofread this before delivering. |
| `shopify/products_en.csv` | Shopify product import (main language). Import with "Overwrite products with matching handles". |
| `shopify/translations.csv` | Other languages, for Shopify's Translate & Adapt CSV import. |
| `woocommerce/products_<lang>.csv` | WooCommerce importer, one file per language, SEO in Yoast fields. |
| `results.json` | Everything, for re-running the PDF or other exports. |

**3. Scrape to an intake sheet first** (when you want to add facts by hand before generating):

```bash
listwell scrape --shopify-shop https://client-shop.com --limit 100 --out intake.csv
```

Add `--dry-run` to `generate` or `sample` to test the whole pipeline without API calls.
Use `listwell pdf out/results.json --prospect "Name"` to rebuild a PDF.

## How generation works

One Claude call per product (`listwell/generate.py`) with the photo(s) and facts, returning
all languages at once as structured JSON, so the output always parses. Defaults:

- Model `claude-opus-5-5` at effort `medium`. Each run prints token use and an approximate
  cost. Switch to `claude-sonnet-5-5` or `claude-haiku-5-5` in `brand.toml` if margins need it,
  after comparing quality on a few real products.
- Server-side refusal fallback is on (`use_fallbacks`), so a rare declined request is retried
  on another model inside the same call.
- The system prompt forbids invented specs and risky EU claims (health, green/eco, "best"),
  and asks for native copy per language rather than translation. Anything it had to assume
  shows up in `review_notes`.
- Products run 4 at a time (`concurrency`); a failed product is reported and the rest finish.

## Before the first real client import

- Export one product from the client's shop and compare column headers with ours. Shopify
  matches products by **Handle**, WooCommerce by **ID/SKU**. Scraped products keep the
  shop's own handle and id, so updates land on existing products.
- For Shopify translations, check that the client has the target languages published and
  compare `translations.csv` with their own Translate & Adapt export.
- Do a test import on 1 to 2 products.

## Layout

```
listwell/
  models.py     product in, copy out (pydantic)
  config.py     brand.toml loader
  sources.py    intake CSV, product page + Shopify scraping
  generate.py   Claude call, prompt, batch runner, offline FakeGenerator
  export.py     Shopify / WooCommerce / review CSVs
  demo_pdf.py   before/after sales PDF
  cli.py        `listwell` command
examples/       sample intake sheet, photos, a sample PDF
tests/          offline tests
```
