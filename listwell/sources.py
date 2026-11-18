"""Where products come from: an intake CSV (e.g. a Google Sheet export) or a live shop page."""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

from .models import ProductInput

USER_AGENT = "Mozilla/5.0 (compatible; ProductCopyBot/0.1)"
LIST_SEP = "|"

INTAKE_COLUMNS = ["sku", "name", "facts", "images", "brand", "category", "price",
                  "source_url", "original_description"]


# ---------------------------------------------------------------- intake CSV

def read_intake_csv(path: str | Path) -> list[ProductInput]:
    """Read the intake sheet. `facts` and `images` are '|'-separated.

    Image paths are resolved relative to the CSV file, so a folder with
    products.csv + photos/ can be zipped and sent around as one unit.
    """
    path = Path(path)
    base = path.parent
    products = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if None in row:  # more cells than headers: usually an unquoted comma
                raise ValueError(f"{path}, line {reader.line_num}: too many columns; "
                                 "quote cells that contain commas")
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not row.get("name"):
                continue
            images = []
            for ref in _split(row.get("images", "")):
                if not ref.startswith(("http://", "https://")) and not Path(ref).is_absolute():
                    ref = str(base / ref)
                images.append(ref)
            products.append(ProductInput(
                sku=row.get("sku", ""), name=row["name"], facts=_split(row.get("facts", "")),
                images=images, brand=row.get("brand", ""), category=row.get("category", ""),
                price=row.get("price", ""), source_url=row.get("source_url", ""),
                original_description=row.get("original_description", ""),
            ))
    return products


def write_intake_csv(products: list[ProductInput], path: str | Path) -> None:
    """Write products (e.g. scraped ones) back out as an editable intake sheet."""
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=INTAKE_COLUMNS + ["shop_handle", "shop_id"])
        w.writeheader()
        for p in products:
            d = p.model_dump()
            d["facts"] = LIST_SEP.join(p.facts)
            d["images"] = LIST_SEP.join(p.images)
            w.writerow({k: d.get(k, "") for k in w.fieldnames})


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(LIST_SEP) if v.strip()]


# ---------------------------------------------------------------- scraping

def _get(url: str, timeout: float = 20) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace"), r.geturl()


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    text = soup.get_text(" ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def _from_shopify_json(p: dict, base_url: str) -> ProductInput:
    variant = (p.get("variants") or [{}])[0]
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return ProductInput(
        sku=variant.get("sku") or "",
        name=p.get("title", ""),
        images=[img["src"] for img in (p.get("images") or [])[:3] if img.get("src")],
        brand=p.get("vendor", ""),
        category=p.get("product_type", ""),
        price=str(variant.get("price") or ""),
        facts=[f"Tag: {t}" for t in tags[:5]],
        source_url=urllib.parse.urljoin(base_url, f"/products/{p.get('handle', '')}"),
        original_description=html_to_text(p.get("body_html") or ""),
        shop_handle=p.get("handle", ""),
        shop_id=str(p.get("id", "")),
    )


def scrape_product(url: str) -> ProductInput:
    """Read one product page. Tries Shopify's .json endpoint, then JSON-LD, then OpenGraph."""
    parsed = urllib.parse.urlparse(url)
    if "/products/" in parsed.path:
        try:
            body, _ = _get(urllib.parse.urlunparse(parsed._replace(path=parsed.path.rstrip("/") + ".json", query="")))
            return _from_shopify_json(json.loads(body)["product"], url)
        except Exception:
            pass  # not Shopify, or JSON disabled - fall through to HTML
    html, final_url = _get(url)
    return parse_product_html(html, final_url)


def parse_product_html(html: str, url: str) -> ProductInput:
    soup = BeautifulSoup(html, "html.parser")
    product = _find_jsonld_product(soup)
    if product:
        offers = product.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        brand = product.get("brand") or ""
        if isinstance(brand, dict):
            brand = brand.get("name", "")
        images = product.get("image") or []
        if isinstance(images, (str, dict)):
            images = [images]
        images = [i.get("url", "") if isinstance(i, dict) else i for i in images]
        return ProductInput(
            sku=str(product.get("sku") or ""),
            name=html_to_text(product.get("name", "")),
            images=[urllib.parse.urljoin(url, i) for i in images if i][:3],
            brand=brand,
            category=str(product.get("category") or ""),
            price=" ".join(str(x) for x in [offers.get("price"), offers.get("priceCurrency")] if x),
            source_url=url,
            original_description=html_to_text(product.get("description", "")),
        )

    def meta(prop):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return tag.get("content", "").strip() if tag else ""

    name = meta("og:title") or (soup.title.get_text(strip=True) if soup.title else "")
    if not name:
        raise ValueError(f"Could not find a product on {url}")
    image = meta("og:image")
    return ProductInput(
        name=name,
        images=[urllib.parse.urljoin(url, image)] if image else [],
        source_url=url,
        original_description=meta("og:description") or meta("description"),
    )


def _find_jsonld_product(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                t = node.get("@type")
                if t == "Product" or (isinstance(t, list) and "Product" in t):
                    return node
                stack.extend(v for k, v in node.items() if k in ("@graph", "mainEntity", "itemListElement", "item"))
    return None


def scrape_shopify_catalog(shop_url: str, limit: int = 25) -> list[ProductInput]:
    """Pull up to `limit` products from a Shopify shop's public /products.json.

    Handy for prospecting: grab a few of a prospect's real products for a free sample.
    """
    base = shop_url.rstrip("/")
    out: list[ProductInput] = []
    page = 1
    while len(out) < limit and page <= 20:
        body, _ = _get(f"{base}/products.json?limit={min(250, limit)}&page={page}")
        items = json.loads(body).get("products", [])
        if not items:
            break
        out.extend(_from_shopify_json(p, base) for p in items)
        page += 1
    return out[:limit]


def _norm(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def pick_products(products: list[ProductInput], wanted: list[str]) -> tuple[list[ProductInput], list[str]]:
    """One product per wanted name: exact match first, else the shortest name containing it."""
    picked, missing = [], []
    for w in wanted:
        nw = _norm(w)
        hits = [p for p in products if nw and nw in _norm(p.name)]
        exact = [p for p in hits if _norm(p.name) == nw]
        best = (exact or sorted(hits, key=lambda p: len(p.name)))[:1]
        if best and best[0] not in picked:
            picked.append(best[0])
        elif not best:
            missing.append(w)
    return picked, missing


def _from_woo_store_json(p: dict, base_url: str) -> ProductInput:
    prices = p.get("prices") or {}
    price = ""
    if prices.get("price"):
        minor = int(prices.get("currency_minor_unit", 2) or 0)
        price = f"{int(prices['price']) / 10 ** minor:.{minor}f} {prices.get('currency_code', '')}".strip()
    desc = html_to_text(p.get("description") or "") or html_to_text(p.get("short_description") or "")
    return ProductInput(
        sku=p.get("sku") or "",
        name=html_to_text(p.get("name", "")),
        images=[i["src"] for i in (p.get("images") or [])[:3] if i.get("src")],
        category=", ".join(c.get("name", "") for c in (p.get("categories") or [])[:2]),
        price=price,
        source_url=p.get("permalink") or base_url,
        original_description=desc,
        shop_handle=p.get("slug", ""),
        shop_id=str(p.get("id", "")),
    )


def search_woocommerce(shop_url: str, term: str, limit: int = 20) -> list[ProductInput]:
    """WooCommerce's public Store API: /wp-json/wc/store/v1/products?search=..."""
    base = shop_url.rstrip("/")
    q = urllib.parse.urlencode({"search": term, "per_page": limit})
    body, _ = _get(f"{base}/wp-json/wc/store/v1/products?{q}")
    items = json.loads(body)
    if not isinstance(items, list):
        raise ValueError("not a WooCommerce Store API response")
    return [_from_woo_store_json(p, base) for p in items]


def find_products(shop_url: str, wanted: list[str], limit: int) -> list[ProductInput]:
    """Products from a shop home page: Shopify catalog, else WooCommerce search, else the page itself."""
    try:
        catalog = scrape_shopify_catalog(shop_url, 1000 if wanted else limit)
        if catalog:
            return catalog
    except Exception:
        pass
    if wanted:
        found: list[ProductInput] = []
        for w in wanted:
            try:
                found += search_woocommerce(shop_url, w)
            except Exception:
                pass
        if found:
            return found
        raise ValueError(f"{shop_url}: not a Shopify or WooCommerce shop I can read; "
                         "pass product page URLs instead")
    return [scrape_product(shop_url)]
