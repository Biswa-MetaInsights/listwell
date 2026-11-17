# Listwell (working name)

Multilingual product descriptions for small web shops. Give it a product photo plus a few
facts (or a product page URL) and it writes shop-ready copy in English, German, French and
Dutch: title, teaser, HTML description, bullets, SEO title, meta description and tags.
It exports files that Shopify and WooCommerce import directly, and a before/after PDF for
sales demos.

The brand name, colours, contact details, model and languages all live in `brand.toml`,
so renaming the business means editing that one file.

## Make a sample PDF (no terminal needed)

One-time setup: add your Anthropic API key as a repository secret.
**Settings → Secrets and variables → Actions → New repository secret**,
name `ANTHROPIC_API_KEY`, paste the key, **Add secret**.

Then, for each prospect:

1. Open the **Actions** tab and pick **Make a sample PDF**.
2. Click **Run workflow**, fill in the shop URL and shop name, and click the green button.
3. When the run turns green (a few minutes), open it and download the `sample-N` file
   at the bottom. It contains `sample.pdf`, one `dm-product-N.png` image per product (for Instagram DMs),
   the import files and `run.log` with the cost.

The shop URL can be a Shopify home page (it reads the first products) or a single
product page. Read the PDF before you send it to anyone.

## Run it on your own computer (optional)

```bash
git clone https://github.com/Biswa-MetaInsights/listwell && cd listwell
python3 -m venv .venv && source .venv/bin/activate     # Python 3.11+
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
listwell sample https://naojewels.com --prospect "Nao Jewels" --limit 5
```

Other commands:

```bash
listwell generate client/products.csv --out client/out     # paid batch from an intake sheet
listwell scrape --shopify-shop https://shop.com --out intake.csv   # products to a sheet first
listwell pdf out/results.json --prospect "Name"            # rebuild a PDF
pytest -q                                                  # offline tests
```

Add `--dry-run` to `generate` or `sample` to test without API calls.

### Intake sheet for paid batches

The client fills a sheet like `examples/products.csv` (a Google Sheet exported to CSV
works). `facts` and `images` are `|`-separated; images are paths next to the CSV or
https URLs. Output:

| File | What it is |
|---|---|
| `review.csv` | One row per product and language, with SEO lengths and review notes. Proofread this before delivering. |
| `shopify/products_en.csv` | Shopify product import (main language). Import with "Overwrite products with matching handles". |
| `shopify/translations.csv` | Other languages, for Shopify's Translate & Adapt CSV import. |
| `woocommerce/products_<lang>.csv` | WooCommerce importer, one file per language, SEO in Yoast fields. |
| `results.json` | Everything, for re-running the PDF or other exports. |

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
.github/        sample workflow (Actions tab) and tests
```
