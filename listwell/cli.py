"""Command line entry point.

    listwell generate examples/products.csv --out out/
    listwell scrape https://shop.example/products/mug --out intake.csv
    listwell sample https://prospect-shop.de --prospect "Prospect GmbH" --limit 5
    listwell pdf out/results.json --prospect "Prospect GmbH"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .export import export_all, load_results_json
from .models import ProductInput, ProductResult

# USD per million tokens (input, output). Keep in sync with Anthropic's pricing page.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def _langs(arg: str | None, cfg) -> list[str]:
    return [l.strip().lower() for l in arg.split(",")] if arg else list(cfg.languages)


def _generator(cfg, dry_run: bool):
    if dry_run:
        from .generate import FakeGenerator
        return FakeGenerator(cfg)
    from .generate import Generator
    return Generator(cfg)


def _run_generation(products: list[ProductInput], cfg, langs, dry_run) -> list[ProductResult]:
    gen = _generator(cfg, dry_run)
    total = len(products)
    done = [0]

    def progress(p, r, err):
        done[0] += 1
        status = "ok" if r else f"FAILED: {err}"
        print(f"  [{done[0]}/{total}] {p.name[:60]} - {status}", file=sys.stderr)

    print(f"Generating {total} product(s) in {', '.join(langs)} with {cfg.model}"
          f"{' (dry run)' if dry_run else ''}...", file=sys.stderr)
    results, failures = gen.generate_many(products, langs, on_done=progress)
    tin = sum(r.input_tokens for r in results)
    tout = sum(r.output_tokens for r in results)
    if tin or tout:
        pin, pout = PRICES.get(cfg.model, (0, 0))
        cost = tin / 1e6 * pin + tout / 1e6 * pout
        per = cost / max(1, len(results))
        print(f"Tokens: {tin:,} in / {tout:,} out. Approx. API cost ${cost:.2f} (${per:.3f}/product).",
              file=sys.stderr)
    if failures:
        print(f"{len(failures)} product(s) failed; rerun them after checking the messages above.",
              file=sys.stderr)
    return results


def cmd_generate(a):
    from .sources import read_intake_csv
    cfg = load_config(a.config)
    langs = _langs(a.langs, cfg)
    products = read_intake_csv(a.input)[: a.limit or None]
    results = _run_generation(products, cfg, langs, a.dry_run)
    for f in export_all(results, langs, Path(a.out)):
        print(f)


def cmd_scrape(a):
    from .sources import scrape_product, scrape_shopify_catalog, write_intake_csv
    products: list[ProductInput] = []
    if a.shopify_shop:
        try:
            products += scrape_shopify_catalog(a.shopify_shop, a.limit)
        except Exception as e:
            print(f"  could not read {a.shopify_shop}/products.json: {e}", file=sys.stderr)
    for url in a.urls:
        try:
            products.append(scrape_product(url))
        except Exception as e:
            print(f"  could not read {url}: {e}", file=sys.stderr)
    write_intake_csv(products, a.out)
    print(f"Wrote {len(products)} product(s) to {a.out}. Add facts in the 'facts' column, then run generate.",
          file=sys.stderr)


def cmd_sample(a):
    """Prospect a shop end-to-end: scrape -> generate -> export -> before/after PDF."""
    from .demo_pdf import build_demo_pdf
    from .sources import find_products, pick_products, scrape_product, write_intake_csv
    cfg = load_config(a.config)
    langs = _langs(a.langs, cfg)
    wanted = [w.strip() for w in (a.only or "").split(",") if w.strip()]
    products: list[ProductInput] = []
    for url in a.urls:
        if "/products/" in url or "/product/" in url:
            products.append(scrape_product(url))
        else:
            products += find_products(url, wanted, a.limit)
    if wanted:
        catalog = products
        products, missing = pick_products(products, wanted)
        if missing:
            print(f"  not found in the shop: {', '.join(missing)}", file=sys.stderr)
            print(f"  the shop returned {len(catalog)} product(s), e.g.:", file=sys.stderr)
            for p in catalog[:40]:
                print(f"    - {p.name}", file=sys.stderr)
    products = products[: a.limit]
    if not products:
        sys.exit("No products found.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    write_intake_csv(products, out / "intake.csv")
    results = _run_generation(products, cfg, langs, a.dry_run)
    for f in export_all(results, langs, out):
        print(f)
    print(build_demo_pdf(results, cfg, out / "sample.pdf", prospect=a.prospect, languages=langs))


def cmd_pdf(a):
    from .demo_pdf import build_demo_pdf
    cfg = load_config(a.config)
    results = load_results_json(Path(a.results))
    langs = _langs(a.langs, cfg)
    out = a.out or str(Path(a.results).with_name("sample.pdf"))
    print(build_demo_pdf(results, cfg, out, prospect=a.prospect, languages=langs))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="listwell", description="Multilingual product descriptions for web shops")
    ap.add_argument("--config", help="brand/settings TOML (default: ./brand.toml if present)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="intake CSV -> descriptions + import files")
    g.add_argument("input")
    g.add_argument("--out", default="out")
    g.add_argument("--langs", help="comma list, first is the shop's main language (default from config)")
    g.add_argument("--limit", type=int, default=0)
    g.add_argument("--dry-run", action="store_true", help="no API calls; placeholder text")
    g.set_defaults(func=cmd_generate)

    s = sub.add_parser("scrape", help="product URLs or a Shopify shop -> intake CSV")
    s.add_argument("urls", nargs="*")
    s.add_argument("--shopify-shop", help="shop root URL; reads its public /products.json")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--out", default="intake.csv")
    s.set_defaults(func=cmd_scrape)

    sm = sub.add_parser("sample", help="scrape a prospect + generate + before/after PDF in one go")
    sm.add_argument("urls", nargs="+", help="shop root URL (Shopify) or product page URLs")
    sm.add_argument("--prospect", default="")
    sm.add_argument("--only", help="comma list of product names (or parts) to pick from the shop")
    sm.add_argument("--limit", type=int, default=5)
    sm.add_argument("--langs")
    sm.add_argument("--out", default="sample")
    sm.add_argument("--dry-run", action="store_true")
    sm.set_defaults(func=cmd_sample)

    p = sub.add_parser("pdf", help="results.json -> before/after PDF")
    p.add_argument("results")
    p.add_argument("--prospect", default="")
    p.add_argument("--langs")
    p.add_argument("--out")
    p.set_defaults(func=cmd_pdf)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
