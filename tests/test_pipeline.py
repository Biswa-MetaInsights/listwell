import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from listwell.cli import main
from listwell.config import BrandConfig, load_config
from listwell.demo_pdf import build_demo_pdf
from listwell.export import export_all, load_results_json
from listwell.generate import FakeGenerator, Generator, GenerationError, _output_schema, build_user_content
from listwell.models import ProductInput, slugify
from listwell.sources import parse_product_html, read_intake_csv, _from_shopify_json

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "products.csv"
LANGS = ["en", "de", "fr", "nl"]


def test_slugify_handles_umlauts():
    assert slugify("Grüne Tasse für Küche") == "gruene-tasse-fuer-kueche"


def test_config_loads_and_rejects_unknown(tmp_path):
    cfg = load_config(ROOT / "brand.toml")
    assert cfg.name == "Listwell" and cfg.languages == LANGS
    bad = tmp_path / "b.toml"
    bad.write_text('[brand]\nnmae = "x"\n')
    with pytest.raises(ValueError):
        load_config(bad)


def test_intake_csv_resolves_images():
    products = read_intake_csv(EXAMPLE)
    assert len(products) == 3
    assert products[0].facts[1] == "350 ml"
    assert Path(products[0].images[0]).exists()


def test_jsonld_parsing():
    html = """<html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[{"@type":"WebPage"},{"@type":"Product","name":"Teekanne Fuji",
    "image":["/img/a.jpg"],"sku":"T-1","brand":{"@type":"Brand","name":"Kyusu"},
    "description":"<p>Gusseisen, 0,8 l</p>","offers":{"price":"49.00","priceCurrency":"EUR"}}]}
    </script></head></html>"""
    p = parse_product_html(html, "https://shop.example/p/teekanne")
    assert p.name == "Teekanne Fuji" and p.brand == "Kyusu" and p.sku == "T-1"
    assert p.images == ["https://shop.example/img/a.jpg"]
    assert p.price == "49.00 EUR" and "Gusseisen" in p.original_description


def test_opengraph_fallback():
    html = '<html><head><meta property="og:title" content="Lamp"><meta property="og:image" content="/l.jpg"></head></html>'
    p = parse_product_html(html, "https://s.example/x")
    assert p.name == "Lamp" and p.images == ["https://s.example/l.jpg"]


def test_shopify_json_keeps_handle_and_id():
    p = _from_shopify_json({"id": 42, "title": "Mug", "handle": "mug-blue", "vendor": "V",
                            "body_html": "<ul><li>350 ml</li></ul>", "tags": "a, b",
                            "variants": [{"sku": "M1", "price": "12.00"}],
                            "images": [{"src": "https://cdn/x.jpg"}]}, "https://s.example")
    assert p.handle == "mug-blue" and p.shop_id == "42" and "350 ml" in p.original_description


def test_prompt_and_schema():
    p = read_intake_csv(EXAMPLE)[0]
    content = build_user_content(p, BrandConfig(), LANGS)
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert "German (de)" in content[-1]["text"] and "350 ml" in content[-1]["text"]
    schema = _output_schema(LANGS)
    item = schema["properties"]["copies"]["items"]
    assert item["properties"]["language"]["enum"] == LANGS
    assert item["additionalProperties"] is False


def _fake_response(payload, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, stop_details=None, model="claude-opus-5-5",
                           content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(payload))],
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=2000))


class _FakeClient:
    def __init__(self, resp):
        self.calls = []
        create = lambda **kw: (self.calls.append(kw), resp)[1]
        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def _payload(langs):
    return {"copies": [{"language": l, "title": f"T {l}", "short_description": "s",
                        "description_html": "<p>x</p>", "bullet_points": ["a"], "seo_title": "st",
                        "meta_description": "m", "tags": ["t"]} for l in langs],
            "review_notes": ["Check origin claim"]}


def test_generator_parses_response_and_uses_fallbacks():
    client = _FakeClient(_fake_response(_payload(LANGS)))
    gen = Generator(BrandConfig(), client=client)
    r = gen.generate(ProductInput(name="Mug", facts=["350 ml"]), LANGS)
    assert r.for_language("de").title == "T de" and r.output_tokens == 2000
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"]["effort"] == "medium" and "thinking" not in call


def test_generator_errors():
    gen = Generator(BrandConfig(), client=_FakeClient(_fake_response(_payload(["en"]))))
    with pytest.raises(GenerationError, match="missing languages"):
        gen.generate(ProductInput(name="Mug"), LANGS)
    gen = Generator(BrandConfig(), client=_FakeClient(_fake_response({}, stop="refusal")))
    with pytest.raises(GenerationError, match="declined"):
        gen.generate(ProductInput(name="Mug"), LANGS)


def test_exports_and_pdf(tmp_path):
    cfg = BrandConfig(name="TestBrand")
    products = read_intake_csv(EXAMPLE)
    results, failures = FakeGenerator(cfg).generate_many(products, LANGS)
    assert not failures and [r.product.sku for r in results] == ["MUG-01", "SCF-07", "CND-03"]
    files = export_all(results, LANGS, tmp_path)
    assert all(f.exists() for f in files)
    with (tmp_path / "shopify" / "products_en.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Handle"] == "stoneware-mug-terra-mug-01" and rows[0]["SEO Title"]
    with (tmp_path / "shopify" / "translations.csv").open(encoding="utf-8-sig") as f:
        tr = list(csv.DictReader(f))
    assert len(tr) == 3 * 3 * 4 and {r["Locale"] for r in tr} == {"de", "fr", "nl"}
    with (tmp_path / "woocommerce" / "products_de.csv").open(encoding="utf-8-sig") as f:
        assert list(csv.DictReader(f))[0]["Name"].startswith("[de]")
    again = load_results_json(tmp_path / "results.json")
    assert again[0].for_language("nl").language == "nl"
    pdf = build_demo_pdf(again, cfg, tmp_path / "s.pdf", prospect="Casa Lume")
    assert pdf.read_bytes()[:4] == b"%PDF" and pdf.stat().st_size > 5000


def test_cli_dry_run(tmp_path):
    main(["--config", str(ROOT / "brand.toml"), "generate", str(EXAMPLE), "--out", str(tmp_path), "--dry-run"])
    assert (tmp_path / "review.csv").exists()
    main(["--config", str(ROOT / "brand.toml"), "pdf", str(tmp_path / "results.json"), "--prospect", "X"])
    assert (tmp_path / "sample.pdf").exists()


def test_pick_products_one_per_name():
    from listwell.sources import pick_products
    ps = [ProductInput(name=n) for n in ["SIGNATURE EARRING", "PETITE SIGNATURE EARRINGS",
                                         "Kerstboom 2026 Nummer 10", "Kerstboom 2026 Nummer 1", "Bague Noëlle"]]
    picked, missing = pick_products(ps, ["Signature Earring", "2026 Nummer 1", "noelle", "Ghost"])
    assert [p.name for p in picked] == ["SIGNATURE EARRING", "Kerstboom 2026 Nummer 1", "Bague Noëlle"]
    assert missing == ["Ghost"]


def test_woo_store_json():
    from listwell.sources import _from_woo_store_json
    p = _from_woo_store_json({"id": 7, "name": "Snoeppot met houten deksel", "slug": "snoeppot",
                              "description": "<p>Glas, 1 l</p>", "prices": {"price": "1995",
                              "currency_minor_unit": 2, "currency_code": "EUR"},
                              "images": [{"src": "https://x/i.jpg"}], "categories": [{"name": "Kerst"}],
                              "permalink": "https://shop/p/snoeppot"}, "https://shop")
    assert p.price == "19.95 EUR" and p.shop_id == "7" and p.original_description == "Glas, 1 l"


def test_pick_products_loose_match():
    from listwell.sources import pick_products
    ps = [ProductInput(name="Serveerschaal Groot - Groen - 27cm"), ProductInput(name="Mok blauw")]
    picked, missing = pick_products(ps, ["Grote serveerschaal groen 27 cm"])
    assert [p.name for p in picked] == ["Serveerschaal Groot - Groen - 27cm"] and not missing
