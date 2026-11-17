"""Before/after PDF to show a prospect what their own products could look like."""

from __future__ import annotations

import html
import io
import re
import urllib.request
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from .config import BrandConfig
from .models import LANGUAGE_NAMES, ProductResult
from .sources import USER_AGENT

_FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/Library/Fonts", str(Path.home() / "Library/Fonts"),
              "C:/Windows/Fonts", str(Path(__file__).parent / "fonts")]


def _register_fonts() -> tuple[str, str]:
    """Use DejaVu (full Unicode) when available, else Helvetica (fine for Western EU languages)."""
    for d in _FONT_DIRS:
        reg, bold = Path(d) / "DejaVuSans.ttf", Path(d) / "DejaVuSans-Bold.ttf"
        if reg.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("Body", str(reg)))
            pdfmetrics.registerFont(TTFont("Body-Bold", str(bold)))
            pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold")
            return "Body", "Body-Bold"
    return "Helvetica", "Helvetica-Bold"


def _html_to_flow(markup: str, style: ParagraphStyle) -> list:
    """Map our limited description HTML (<p>, <ul>, <li>, <strong>) to reportlab paragraphs."""
    soup = BeautifulSoup(markup or "", "html.parser")
    out = []

    def inline(node) -> str:
        parts = []
        for child in node.children:
            if getattr(child, "name", None) in ("strong", "b"):
                parts.append(f"<b>{html.escape(child.get_text())}</b>")
            elif getattr(child, "name", None) is None:
                parts.append(html.escape(str(child)))
            else:
                parts.append(html.escape(child.get_text()))
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    blocks = soup.find_all(["p", "li"]) or [soup]
    for b in blocks:
        text = inline(b)
        if not text:
            continue
        if b.name == "li":
            out.append(Paragraph(text, style, bulletText="•"))
        else:
            out.append(Paragraph(text, style))
    return out


def _load_image(ref: str, max_w: float, max_h: float) -> Image | None:
    try:
        if ref.startswith(("http://", "https://")):
            req = urllib.request.Request(ref, headers={"User-Agent": USER_AGENT})
            data = urllib.request.urlopen(req, timeout=20).read()
            src = io.BytesIO(data)
        else:
            src = ref
        iw, ih = ImageReader(src).getSize()
        if isinstance(src, io.BytesIO):
            src.seek(0)
        scale = min(max_w / iw, max_h / ih)
        return Image(src, width=iw * scale, height=ih * scale)
    except Exception:
        return None


class _Styles:
    def __init__(self, cfg: BrandConfig):
        body, bold = _register_fonts()
        primary = colors.HexColor(cfg.primary_color)
        self.primary, self.accent = primary, colors.HexColor(cfg.accent_color)
        self.grey = colors.HexColor("#6B7280")
        self.body = ParagraphStyle("body", fontName=body, fontSize=9.5, leading=13.5, alignment=TA_LEFT,
                                   spaceAfter=4, bulletIndent=2, leftIndent=0)
        self.bullet = ParagraphStyle("bullet", parent=self.body, leftIndent=10)
        self.before = ParagraphStyle("before", parent=self.body, textColor=self.grey)
        self.small = ParagraphStyle("small", parent=self.body, fontSize=8, leading=11, textColor=self.grey)
        self.h1 = ParagraphStyle("h1", fontName=bold, fontSize=24, leading=29, textColor=primary, spaceAfter=8)
        self.h2 = ParagraphStyle("h2", fontName=bold, fontSize=15, leading=19, textColor=primary, spaceAfter=6)
        self.label = ParagraphStyle("label", fontName=bold, fontSize=8, leading=10, textColor=self.grey,
                                    spaceAfter=4)
        self.label_after = ParagraphStyle("label_after", parent=self.label, textColor=self.accent)
        self.title_after = ParagraphStyle("title_after", fontName=bold, fontSize=11.5, leading=15,
                                          spaceAfter=4)
        self.lang = ParagraphStyle("lang", fontName=bold, fontSize=9, leading=12, textColor=primary)
        self.font, self.bold = body, bold


def build_demo_pdf(results: list[ProductResult], cfg: BrandConfig, out_path: str | Path,
                   prospect: str = "", languages: list[str] | None = None) -> Path:
    languages = languages or cfg.languages
    primary_lang = languages[0]
    st = _Styles(cfg)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def frame(canvas, doc):
        w, h = A4
        canvas.saveState()
        canvas.setFillColor(st.primary)
        canvas.rect(0, h - 1.1 * cm, w, 1.1 * cm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont(st.bold, 11)
        canvas.drawString(1.8 * cm, h - 0.72 * cm, cfg.name)
        canvas.setFont(st.font, 8)
        canvas.drawRightString(w - 1.8 * cm, h - 0.72 * cm, cfg.tagline[:95])
        canvas.setFillColor(st.grey)
        footer = " · ".join(x for x in [cfg.company, cfg.website, cfg.contact_email] if x)
        canvas.drawString(1.8 * cm, 1.0 * cm, footer)
        canvas.drawRightString(w - 1.8 * cm, 1.0 * cm, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
                            topMargin=2.0 * cm, bottomMargin=1.8 * cm,
                            title=f"{cfg.name} sample{' for ' + prospect if prospect else ''}",
                            author=cfg.company)
    width = doc.width
    story: list = []

    # Cover
    story += [Spacer(1, 3 * cm), Paragraph(html.escape(cfg.name), st.h1),
              Paragraph(html.escape(cfg.tagline), st.body), Spacer(1, 1 * cm)]
    if prospect:
        story.append(Paragraph(f"Free sample prepared for <b>{html.escape(prospect)}</b>", st.h2))
    lang_names = ", ".join(LANGUAGE_NAMES.get(l, l) for l in languages)
    story += [
        Paragraph(f"{len(results)} of your products, rewritten in {lang_names}.", st.body),
        Paragraph("Each page shows the current text next to the new version, with the other "
                  "languages below. Every text comes with SEO title and meta description, ready "
                  "to import into Shopify or WooCommerce.", st.body),
        Spacer(1, 0.6 * cm),
        Paragraph(date.today().strftime("%d %B %Y"), st.small),
        PageBreak(),
    ]

    for i, r in enumerate(results):
        p, after = r.product, r.for_language(primary_lang)
        if not after:
            continue
        story.append(Paragraph(html.escape(p.name), st.h2))

        # Photo + facts row
        img = next((im for im in (_load_image(ref, 5.5 * cm, 5.5 * cm) for ref in p.images) if im), None)
        meta_lines = [f"<b>{k}:</b> {html.escape(v)}" for k, v in
                      [("Brand", p.brand), ("Category", p.category), ("SKU", p.sku), ("Price", p.price)] if v]
        meta = [Paragraph(m, st.small) for m in meta_lines]
        meta += [Paragraph("<b>SEO title:</b> " + html.escape(after.seo_title)
                           + f" <font color='#6B7280'>({len(after.seo_title)} chars)</font>", st.small),
                 Paragraph("<b>Meta description:</b> " + html.escape(after.meta_description), st.small),
                 Paragraph("<b>Tags:</b> " + html.escape(", ".join(after.tags)), st.small)]
        if img:
            top = Table([[img, meta]], colWidths=[6 * cm, width - 6 * cm])
        else:
            top = Table([[meta]], colWidths=[width])
        top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story += [top, Spacer(1, 0.35 * cm)]

        # Before / after
        before_text = p.original_description.strip()
        before = [Paragraph("BEFORE", st.label)]
        if before_text:
            words = before_text.split()
            clipped = " ".join(words[:220]) + (" …" if len(words) > 220 else "")
            before += [Paragraph(html.escape(line), st.before) for line in clipped.split("\n") if line.strip()]
        else:
            before.append(Paragraph("<i>No description on the product page.</i>", st.before))
        after_flow = [Paragraph(f"AFTER · {LANGUAGE_NAMES.get(primary_lang, primary_lang).upper()}", st.label_after),
                      Paragraph(html.escape(after.title), st.title_after)]
        after_flow += _html_to_flow(after.description_html, st.body)
        ba = Table([[before, after_flow]], colWidths=[width * 0.42, width * 0.58])
        ba.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#F3F4F6")),
            ("BOX", (1, 0), (1, 0), 0.8, st.accent),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story += [ba, Spacer(1, 0.4 * cm)]

        # Other languages
        others = [l for l in languages[1:] if r.for_language(l)]
        if others:
            rows = []
            for l in others:
                c = r.for_language(l)
                rows.append([Paragraph(l.upper(), st.lang),
                             [Paragraph(f"<b>{html.escape(c.title)}</b>", st.body),
                              Paragraph(html.escape(c.short_description), st.body)]])
            t = Table(rows, colWidths=[1.2 * cm, width - 1.2 * cm])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.HexColor("#E5E7EB")),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
            story.append(KeepTogether([Paragraph("ALSO IN", st.label), t]))
        if i < len(results) - 1:
            story.append(PageBreak())

    # Closing page
    story += [PageBreak(), Spacer(1, 3 * cm), Paragraph("Want this for your whole catalogue?", st.h2),
              Paragraph("We deliver ready-to-import files for Shopify and WooCommerce, checked by a "
                        "person before they reach you.", st.body)]
    contact = " · ".join(x for x in [cfg.website, cfg.contact_email] if x)
    if contact:
        story.append(Paragraph(html.escape(contact), st.body))

    doc.build(story, onFirstPage=frame, onLaterPages=frame)
    return out_path
