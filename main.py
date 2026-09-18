"""Our Legacy scraper — Centra/Next.js PDP scrape, local SigLIP embed, Supabase upsert."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import cfg
from embeddings import embed_products
from parser import scrape_all_products
from supabase_client import (
    SupabaseClient,
    handle_stale_products,
    load_stale_tracker,
    save_stale_tracker,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("scraper-ourlegacy")


COMPARE_FIELDS = [
    "title",
    "affiliate_url",
    "image_url",
    "compressed_image_url",
    "back_image_url",
    "brand",
    "description",
    "category",
    "gender",
    "price",
    "sale",
    "metadata",
    "size",
    "second_hand",
    "country",
    "tags",
    "additional_images",
    "other",
]


def _needs_update(existing: dict[str, Any], scraped: dict[str, Any]) -> bool:
    """True when any scraped field changed or an embedding is missing."""
    for field in COMPARE_FIELDS:
        if str(existing.get(field) or "") != str(scraped.get(field) or ""):
            return True
    if scraped.get("image_url") and not existing.get("image_embedding"):
        return True
    if scraped.get("back_image_url") and not existing.get("back_image_embedding"):
        return True
    if not existing.get("info_embedding"):
        return True
    return False


def _to_db_row(record: dict[str, Any]) -> dict[str, Any]:
    row = {
        "id": record["id"],
        "source": record["source"],
        "product_url": record["product_url"],
        "affiliate_url": record.get("affiliate_url"),
        "image_url": record["image_url"],
        "compressed_image_url": record.get("compressed_image_url"),
        "back_image_url": record.get("back_image_url"),
        "brand": record.get("brand"),
        "title": record["title"],
        "description": record.get("description"),
        "category": record.get("category"),
        "gender": record.get("gender"),
        "price": record.get("price"),
        "sale": record.get("sale"),
        "metadata": record.get("metadata"),
        "size": record.get("size"),
        "second_hand": record.get("second_hand", False),
        "country": record.get("country"),
        "tags": record.get("tags"),
        "additional_images": record.get("additional_images"),
        "other": record.get("other"),
    }
    if record.get("image_embedding"):
        row["image_embedding"] = record["image_embedding"]
    if record.get("back_image_embedding"):
        row["back_image_embedding"] = record["back_image_embedding"]
    if record.get("info_embedding"):
        row["info_embedding"] = record["info_embedding"]
    return {k: v for k, v in row.items() if v is not None}


def _embed_diff_stats() -> dict[str, int]:
    return {
        "front_embeddings": 0,
        "back_embeddings": 0,
        "text_embeddings": 0,
        "skipped": 0,
    }


def run() -> dict[str, Any]:
    logger.info("=== Our Legacy scraper start ===")
    if not cfg.SUPABASE_URL or not cfg.SUPABASE_KEY:
        logger.error("SUPABASE_URL / SUPABASE_KEY env vars missing")
        sys.exit(1)

    scraped = scrape_all_products()
    if not scraped:
        logger.error("No products scraped")
        return {"new": 0, "updated": 0, "unchanged": 0, "upsert_ok": 0, "upsert_fail": 0}

    supa = SupabaseClient()
    existing = supa.fetch_existing_products(cfg.SOURCE)

    to_embed: list[dict[str, Any]] = []
    unchanged = 0
    for record in scraped:
        url = record["product_url"]
        if url not in existing:
            to_embed.append(record)
        elif _needs_update(existing[url], record):
            prev = existing[url]
            record["_existing"] = {
                "image_url": prev.get("image_url"),
                "back_image_url": prev.get("back_image_url"),
                "image_embedding": prev.get("image_embedding"),
                "back_image_embedding": prev.get("back_image_embedding"),
                "info_embedding": prev.get("info_embedding"),
            }
            to_embed.append(record)
        else:
            unchanged += 1

    logger.info(
        "Diff: %d to process, %d unchanged, %d existing in DB",
        len(to_embed),
        unchanged,
        len(existing),
    )

    existing_embeddings: dict[str, dict[str, Any]] = {}
    for purl, row in existing.items():
        existing_embeddings[purl] = {
            "image_url": row.get("image_url"),
            "back_image_url": row.get("back_image_url"),
            "image_embedding": row.get("image_embedding"),
            "back_image_embedding": row.get("back_image_embedding"),
            "info_embedding": row.get("info_embedding"),
        }

    products_embedded, embed_stats = embed_products(
        to_embed,
        existing_embeddings=existing_embeddings,
        source=cfg.SOURCE,
    )

    rows = [_to_db_row(r) for r in products_embedded]
    ok, fail = supa.upsert_products(rows, batch_size=cfg.BATCH_SIZE)

    seen_urls = {p["product_url"] for p in scraped}
    stale_tracker = load_stale_tracker()
    deleted, updated_tracker = handle_stale_products(
        supa,
        cfg.SOURCE,
        seen_urls,
        existing,
        stale_tracker,
        threshold=cfg.STALE_MISS_THRESHOLD,
    )
    save_stale_tracker(updated_tracker)

    summary = {
        "new": sum(1 for r in products_embedded if r["product_url"] not in existing),
        "updated": sum(1 for r in products_embedded if r["product_url"] in existing),
        "unchanged": unchanged,
        "upsert_ok": ok,
        "upsert_fail": fail,
        "front_embeddings": embed_stats.get("front_embeddings", 0),
        "back_embeddings": embed_stats.get("back_embeddings", 0),
        "text_embeddings": embed_stats.get("text_embeddings", 0),
        "stale_deleted": deleted,
        "total_scraped": len(scraped),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }

    Path("logs").mkdir(exist_ok=True)
    Path("logs/last_run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    logger.info("=== Run summary ===")
    for k, v in summary.items():
        logger.info("%s: %s", k, v)
    logger.info("=== Our Legacy scraper complete ===")
    return summary


if __name__ == "__main__":
    run()