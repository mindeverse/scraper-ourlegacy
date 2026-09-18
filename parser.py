"""Our Legacy parser — Centra/Next.js sitemap discovery + PDP __NEXT_DATA__ parse."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from config import cfg

logger = logging.getLogger(__name__)

# Back-view detection: look for "back" / "rear" markers in image blob or filename.
BACK_KEYWORDS = (
    "back",
    "rear",
    "backview",
    "back_view",
    "_b_",
    "-back",
    "_back",
    "_b.",
)

# Locale variants we must never crawl (robots disallowed).
DISALLOWED_SEGMENTS = ("/global-en", "/jp-ja", "/jp-en")

HUB_MARKERS = ("new-arrivals", "core-collection")

# Category URIs that are gender hubs (excluded from gender inference).
GENDER_HUB_MARKERS = ("new-arrivals", "core-collection")


def _headers() -> dict[str, str]:
    return {
        "User-Agent": cfg.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    }


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"{cfg.BASE_URL}{url}"
    return url


def _stable_id(product_url: str) -> str:
    digest = hashlib.sha256(f"{cfg.SOURCE}:{product_url}".encode()).hexdigest()[:24]
    return f"ourlegacy_{digest}"


# ---------------------------------------------------------------- sitemap discovery

def fetch_sitemap_index() -> list[str]:
    """Return the list of product sitemap URLs from the sitemap index."""
    sitemaps: list[str] = []
    try:
        resp = requests.get(cfg.SITEMAP_INDEX_URL, headers=_headers(), timeout=cfg.REQUEST_TIMEOUT)
        resp.raise_for_status()
        for m in re.finditer(r"<loc>\s*([^<]+?)\s*</loc>", resp.text):
            url = m.group(1).strip()
            if "/sitemap/product/" in url:
                sitemaps.append(url)
    except Exception as e:
        logger.error("Failed to fetch sitemap index %s: %s", cfg.SITEMAP_INDEX_URL, e)
    logger.info("Found %d product sitemaps in index", len(sitemaps))
    return sitemaps


def fetch_product_urls_from_sitemap(sitemap_url: str) -> list[str]:
    """Return product page URLs from a product sitemap (sub-sitemap URLs cross-listed)."""
    urls: list[str] = []
    try:
        resp = requests.get(sitemap_url, headers=_headers(), timeout=cfg.REQUEST_TIMEOUT)
        resp.raise_for_status()
        # fetchable product URLs are bare paths (no .xml suffix), page URLs for PDPS
        for m in re.finditer(r"<loc>\s*([^<]+?)\s*</loc>", resp.text):
            url = m.group(1).strip()
            if any(seg in url for seg in DISALLOWED_SEGMENTS):
                continue
            if url.endswith(".xml"):
                continue
            urls.append(url)
    except Exception as e:
        logger.error("Failed to fetch sitemap %s: %s", sitemap_url, e)
    return urls


def discover_all_product_urls() -> list[str]:
    """Discover all product URLs via sitemap index -> product sitemaps."""
    seen: set[str] = set()
    urls: list[str] = []
    for sitemap in fetch_sitemap_index():
        page_urls = fetch_product_urls_from_sitemap(sitemap)
        for u in page_urls:
            canonical = urlparse(u)._replace(query="", fragment="").geturl().rstrip("/")
            if canonical not in seen:
                seen.add(canonical)
                urls.append(canonical)
        time.sleep(0.4)
    logger.info("Discovered %d unique product URLs", len(urls))
    return urls


# ---------------------------------------------------------------- PDP parsing

def _extract_next_data(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return {}
    try:
        return json.loads(script.string)
    except Exception as e:
        logger.warning("Failed to parse __NEXT_DATA__ JSON: %s", e)
        return {}


def _money(raw: Any) -> Optional[str]:
    """Normalize '590.00 EUR' / '590.00 EUR' -> '590.00EUR'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return f"{float(raw):.2f}{cfg.CURRENCY}"
    raw = str(raw).strip().replace("\xa0", " ")
    raw = raw.replace("  ", " ").strip()
    m = re.search(r"([\d\s.,]+?)\s*([A-Za-z]{2,3})\s*$", raw)
    if not m:
        return None
    amount = m.group(1).replace(" ", "").replace("\u00a0", "")
    if amount.count(",") == 1 and amount.count(".") == 0:
        amount = amount.replace(",", ".")
    elif amount.count(",") > 1:
        amount = amount.replace(",", "")
    elif "." in amount and "," in amount:
        amount = amount.replace(",", "")
    try:
        val = float(amount)
    except ValueError:
        return None
    return f"{val:.2f}{m.group(2).upper()}"


def _extract_price_info(prices: dict[str, Any], display_markets: list[int]) -> tuple[Any, Any]:
    """Return (price_money, sale_money) from Centra prices map."""
    chosen = None
    for market in display_markets:
        entry = prices.get(str(market))
        if entry is None:
            continue
        if isinstance(entry, dict):
            chosen = entry
            break
        chosen = {"price": entry, "priceBeforeDiscount": entry, "showAsOnSale": False}
        break

    if not chosen:
        return None, None

    show_on_sale = bool(chosen.get("showAsOnSale"))
    discount = chosen.get("discountPercent") or 0
    current = chosen.get("price") or chosen.get("priceAsNumber")
    before = chosen.get("priceBeforeDiscount") or chosen.get("priceBeforeDiscountAsNumber")
    price_money = _money(current)
    if show_on_sale or float(discount or 0) > 0 or (price_money and _money(before) and _money(before) != price_money):
        return _money(before) or price_money, price_money
    return price_money, None


def _angle_fingerprint(url: str) -> str:
    path = urlparse(url).path
    path = re.sub(r"-(rtail-big|rtail-standard|full|standard|thumb|mini)(\.\w+)?$", "", path)
    return path


def _media_full_urls(product: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    """Return (ordered full-size URLs deduped by angle, media objects with url+label)."""
    full_urls: list[str] = []
    objects: list[dict[str, str]] = []
    seen_angles: set[str] = set()
    media_standard: list[str] = []

    def _add(url: str, label: str = "") -> None:
        if not url:
            return
        fp = _angle_fingerprint(url)
        if fp in seen_angles:
            return
        seen_angles.add(fp)
        full_urls.append(url)
        objects.append({"full": url, "alt": label})

    def _first_url(candidates: Any) -> str:
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                return _normalize_url(str(first.get("url") or ""))
            if isinstance(first, str):
                return _normalize_url(first)
        return ""

    media_raw = product.get("media") or {}
    if isinstance(media_raw, dict):
        for source in media_raw.get("full") or []:
            _add(_normalize_url(str(source)))
        media_standard = [
            _normalize_url(str(s)) for s in (media_raw.get("standard") or [])
            if s
        ]
    elif isinstance(media_raw, list):
        for source in media_raw:
            _add(_normalize_url(str(source)))

    for obj in product.get("mediaObjects") or []:
        if not isinstance(obj, dict):
            continue
        label = ""
        for key in ("altText", "alt", "title"):
            val = obj.get(key)
            if val:
                label = str(val)
                break
        sources = obj.get("sources") or {}
        full_url = _first_url(sources.get("full"))
        if not full_url:
            full_url = _first_url(sources.get("standard"))
        _add(full_url, label)

    return full_urls, objects, media_standard[0] if media_standard else ""


def _detect_back_image(objects: list[dict[str, str]], front_src: str) -> Optional[str]:
    """Pick first gallery image whose CDN filename (or a short angle-only alt label)
    looks like a back view. Long descriptive alt text is ignored."""
    for obj in objects:
        src = obj.get("full") or ""
        if not src or src == front_src:
            continue
        path = urlparse(src).path.lower()
        if any(k in path for k in BACK_KEYWORDS):
            return src
    for obj in objects:
        src = obj.get("full") or ""
        if not src or src == front_src:
            continue
        alt = (obj.get("alt") or "").strip().lower()
        if len(alt) <= 40 and any(k in alt for k in BACK_KEYWORDS):
            return src
    return None


def _category_uris(categories: list[Any]) -> list[str]:
    uris: list[str] = []
    for c in categories or []:
        if isinstance(c, str):
            uris.append(c.strip().lstrip("/"))
        elif isinstance(c, dict):
            uri = (c.get("uri") or "").strip().lstrip("/")
            if uri:
                uris.append(uri)
    return uris


def _build_category(uris: list[str]) -> Optional[str]:
    if not uris:
        return None
    names: set[str] = set()
    for uri in uris:
        if any(m in uri for m in HUB_MARKERS):
            continue
        label = cfg.CATEGORY_MAP.get(uri)
        if label:
            names.add(label)
    if not names:
        for uri in uris:
            if any(m in uri for m in HUB_MARKERS):
                continue
            names.add(uri.replace("/", " ").title())
    return ", ".join(sorted(names)) if names else None


def _infer_gender(uris: list[str]) -> str:
    meaningful = [u for u in uris if not any(m in u for m in GENDER_HUB_MARKERS)]
    blob = " ".join(meaningful).lower()
    if re.search(r"\bwomens?\b|woman|women", blob):
        return "Women"
    if blob.startswith("womens"):
        return "Women"
    if blob.startswith("mens"):
        return "Men"
    if re.search(r"\bmens?\b|man\b", blob):
        return "Men"
    return cfg.GENDER_DEFAULT


def _clean_text(raw: Any) -> Optional[str]:
    if not raw:
        return None
    text = re.sub(r"<[^>]+>", " ", str(raw))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _item_names(product: dict[str, Any]) -> tuple[list[str], bool]:
    sizes: list[str] = []
    any_stock = False
    for item in product.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if name and name not in sizes:
            sizes.append(name)
        stock = item.get("stock")
        if isinstance(stock, str):
            if stock in ("yes", "few", "in", "1", "2"):
                any_stock = True
        warehouses = item.get("warehouses") or []
        for wh in warehouses:
            if isinstance(wh, dict):
                if any(isinstance(v, (int, float)) and v > 0 for v in wh.values()):
                    any_stock = True
            elif isinstance(wh, (int, float)) and wh > 0:
                any_stock = True
            elif isinstance(wh, dict) and wh.get("stock") not in (0, "0", None, "no"):
                any_stock = True
    return sizes, any_stock


def parse_product_page(product_url: str) -> Optional[dict[str, Any]]:
    """Fetch a PDP and parse the Centra product from __NEXT_DATA__."""
    try:
        resp = requests.get(product_url, headers=_headers(), timeout=cfg.REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", product_url, e)
        return None
    data = _extract_next_data(resp.text)
    if not data:
        logger.warning("No __NEXT_DATA__ for %s", product_url)
        return None

    page_props = (data.get("props") or {}).get("pageProps") or {}
    centra = (page_props.get("pageProps") or {}).get("centra") or {}
    product = centra.get("product") or {}

    if not product:
        logger.warning("No centra.product for %s", product_url)
        return None

    name = (product.get("name") or "").strip()
    variant_name = (product.get("variantName") or "").strip()
    title = name or "Our Legacy Product"
    if variant_name and variant_name.lower() not in name.lower():
        title = f"{name} {variant_name}".strip()

    media_full, media_objects, compressed_url = _media_full_urls(product)
    if not media_full:
        logger.warning("No images for %s", product_url)
        return None

    front = media_full[0]
    back_image_url = _detect_back_image(media_objects, front)

    additional = [u for u in media_full[1:] if u != front]
    if back_image_url and back_image_url not in additional:
        additional.append(back_image_url)
    additional_images = " , ".join(additional) if additional else None

    prices = product.get("prices") or {}
    price, sale = _extract_price_info(prices, [cfg.PRICE_MARKET_EUR, cfg.PRICE_MARKET_USD])

    uris = _category_uris(product.get("categories"))
    category = _build_category(uris)
    gender = _infer_gender(uris)

    description = _clean_text(
        product.get("description") or product.get("descriptionHtml") or product.get("excerpt")
    )
    composition = _clean_text(product.get("var_composition_text")) or None
    excerpt = _clean_text(product.get("excerpt"))

    sizes, any_stock = _item_names(product)
    availability = bool(product.get("available", any_stock))

    sku = (product.get("sku") or product.get("productSku") or "").strip() or None
    collection = (product.get("collectionName") or "").strip() or None
    coo = (
        (product.get("countryOrigin") or "").strip()
        or (product.get("var_coo_text") or "").strip()
        or None
    )
    class_text = (product.get("Class_text") or "").strip() or None
    department = (product.get("Department_text") or "").strip() or None
    knit_woven = (product.get("var_knit_woven_text") or "").strip() or None

    colors: list[str] = []
    if variant_name:
        colors.append(variant_name)
    for swatch in product.get("colorSwatches") or []:
        if isinstance(swatch, dict):
            sw_name = (swatch.get("name") or swatch.get("color") or "").strip()
            if sw_name and sw_name not in colors:
                colors.append(sw_name)

    tags: list[str] = []
    for uri in uris:
        tags.append(uri)
    if department:
        tags.append(department)

    has_sale = bool(sale)

    metadata = {
        "uri": product.get("uri"),
        "sku": sku,
        "centraProduct": product.get("centraProduct"),
        "centraVariant": product.get("centraVariant"),
        "collection": collection,
        "variant_name": variant_name,
        "colors": colors,
        "sizes": sizes,
        "availability": availability,
        "composition": composition,
        "country_of_origin": coo,
        "class": class_text,
        "department": department,
        "knit_woven": knit_woven,
        "categories": uris,
        "excerpt": excerpt,
        "on_sale": has_sale,
        "currency": cfg.CURRENCY,
        "scrape_source": "sitemap+centra_pdp",
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    metadata = {k: v for k, v in metadata.items() if v is not None and v != [] and v != {}}

    return {
        "id": _stable_id(product_url.rstrip("/")),
        "source": cfg.SOURCE,
        "product_url": product_url.rstrip("/"),
        "affiliate_url": None,
        "image_url": front,
        "compressed_image_url": compressed_url or None,
        "back_image_url": back_image_url,
        "brand": cfg.BRAND_COLUMN,
        "title": title,
        "description": description,
        "category": category,
        "gender": gender,
        "price": price,
        "sale": sale,
        "metadata": json.dumps(metadata, ensure_ascii=False),
        "size": ", ".join(sizes) if sizes else None,
        "second_hand": cfg.SECOND_HAND,
        "country": cfg.COUNTRY,
        "tags": tags or None,
        "additional_images": additional_images,
        "other": None,
    }


def scrape_all_products(urls: list[str] | None = None) -> list[dict[str, Any]]:
    """Scrape every discovered product URL."""
    if urls is None:
        urls = discover_all_product_urls()
    products: list[dict[str, Any]] = []
    failed: list[str] = []
    for idx, url in enumerate(urls, 1):
        record = parse_product_page(url)
        if record:
            products.append(record)
        else:
            failed.append(url)
        if idx % 50 == 0:
            logger.info("  Scraped %d/%d products", idx, len(urls))
        time.sleep(cfg.RATE_LIMIT_DELAY)
    logger.info("Scraped %d products, %d URLs failed to parse", len(products), len(failed))
    if failed:
        with open("logs/missed_urls.log", "a") as f:
            for u in failed:
                f.write(f"{u}\n")
    return products