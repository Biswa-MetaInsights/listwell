"""Data models for products going in and copy coming out."""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field


LANGUAGE_NAMES = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "nl": "Dutch",
    "it": "Italian",
    "es": "Spanish",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
}


def slugify(text: str) -> str:
    text = text.lower()
    for a, b in {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "è": "e",
                 "ê": "e", "ë": "e", "à": "a", "â": "a", "ç": "c", "ï": "i",
                 "î": "i", "ô": "o", "û": "u", "ù": "u"}.items():
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:80] or "product"


class ProductInput(BaseModel):
    """One product as the shop gives it to us: a photo plus a few facts."""

    sku: str = ""
    name: str
    facts: list[str] = Field(default_factory=list, description="Hard facts: material, size, use...")
    images: list[str] = Field(default_factory=list, description="Local paths or https URLs")
    brand: str = ""
    category: str = ""
    price: str = ""
    source_url: str = ""
    original_description: str = Field("", description="The shop's current text (the 'before')")
    shop_handle: str = Field("", description="Existing URL handle/slug in the shop, if known")
    shop_id: str = Field("", description="Existing product id in the shop, if known")

    @property
    def handle(self) -> str:
        if self.shop_handle:
            return self.shop_handle
        return slugify(f"{self.name}-{self.sku}" if self.sku else self.name)


class LocalizedCopy(BaseModel):
    """All shop-facing copy for one product in one language."""

    language: str = Field(description="ISO 639-1 code, e.g. 'de'")
    title: str = Field(description="Product title as shown in the shop, max ~70 chars")
    short_description: str = Field(description="1-2 sentence teaser, plain text")
    description_html: str = Field(
        description="Full description as simple HTML: <p>, <ul>, <li>, <strong> only"
    )
    bullet_points: list[str] = Field(description="3-5 key benefits, plain text")
    seo_title: str = Field(description="Max 60 characters")
    meta_description: str = Field(description="Max 155 characters")
    tags: list[str] = Field(description="3-8 search tags in this language")


class GeneratedCopy(BaseModel):
    """What the model returns for one product: every requested language plus review notes."""

    copies: list[LocalizedCopy]
    review_notes: list[str] = Field(
        description="Things a human should check: missing facts, assumptions made, "
        "claims that need proof. Empty list if nothing."
    )


class ProductResult(BaseModel):
    product: ProductInput
    copy_: GeneratedCopy = Field(alias="copy")
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    model_config = {"populate_by_name": True}

    def for_language(self, lang: str) -> Optional[LocalizedCopy]:
        return next((c for c in self.copy_.copies if c.language == lang), None)
