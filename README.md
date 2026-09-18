# Our Legacy scraper

Production scraper for **Our Legacy** — Centra/Next.js storefront. Reads the product
sitemap, parses each PDP's `__NEXT_DATA__.props.pageProps.pageProps.centra.product`
blob, embeds product images + text locally with SigLIP, and upserts into the Finds
Supabase `products` table.

- `source`: `scraper-ourlegacy`
- `brand`: `Our Legacy`
- `second_hand`: `false`
- Product embedding model: `google/siglip-base-patch16-384` (768-d), run **locally** via
  `transformers` + `torch`. No HF token, no Inference API, no Gemini.
- Upsert unique key: `(source, product_url)`; `id` is stable (hash of source+url).

## How it works

1. **Discovery** — parse `https://www.ourlegacy.com/sitemap/sitemap.xml` (sitemap index)
   → 82 product sitemaps → ~2500 unique product URLs. Locales in robots.txt
   (`/global-en`, `/jp-*`) are skipped.
2. **PDP parse** — GET each product page, extract the `<script id="__NEXT_DATA__">`
   blob, read `props.pageProps.pageProps.centra.product`.
3. **Images** — front packshot is `media.full[0]`. Gallery = remaining `media.full`
   URLs (plus `mediaObjects` fulls). `image_url` always stays the front packshot (the
   iOS app displays this).
4. **Back-view detection** — an image is treated as a back view when its CDN path or
   alt/title text contains a marker keyword: `back`, `rear`, `backview`, `back_view`,
   `_b_`, `-back`, `_back`, `_b.`. If found → `back_image_url` + `back_image_embedding`
   + included in `additional_images`. Otherwise both back columns stay NULL.
5. **Embeddings** — local SigLIP: images via `SiglipImageProcessor` +
   `model.get_image_features()`, text via `model.get_text_features()`. Text input is
   built from brand/title/category/gender/price/description/colors/sizes/composition.
   Batching (download + inference), checkpoint/resume, and SIGTERM handler included for
   long CI jobs.
6. **Smart upsert** — fetch existing rows for this source in `.range()` pages of 1000;
   deep-compare scraped vs stored; skip unchanged rows entirely. Only changed/new rows
   are embedded. Upsert batches of size 5 with single-row fallback + exponential
   backoff; hard failures are logged to `logs/failed_products.log`.
7. **Stale cleanup** — products not seen this run get a miss count in the stale
   tracker; two consecutive misses → DELETE from the table.

## Fields

- `price` / `sale`: EUR market (`prices["3"]`) normalized to `590.00EUR`; `price` keeps
  the original pre-discount amount, `sale` set only when the product is on sale.
- `category`: mapped from Centra category URIs (e.g. `mens/knitwear` → `Knitwear`);
  multiple labels comma-joined; `new-arrivals`/`core-collection` hubs dropped. Fallback
  title-cases the raw URI.
- `gender`: inferred from category URIs (`womens/...` → Women, `mens/...` → Men),
  default `Unisex`.
- `additional_images`: `url1 , url2 , url3` (space-comma-space), primary front image
  excluded.
- `metadata`: JSON with uri, sku, centra ids, collection, variant name, colors, sizes,
  stock, availability, composition, COO, department, excerpt, categories, scrape time.

## Schema note

`embedding_version` is **not** sent to Supabase (not in the live table; sending it
raises PGRST204). Null embedding fields are omitted from upsert payloads (avoids
PGRST102 key mismatch on nullable vector columns).

## Run

```bash
cp .env.example .env   # fill SUPABASE_URL + SUPABASE_KEY
pip install -r requirements.txt
python main.py
```

## CI

`.github/workflows/scrape.yml` runs on a schedule (`19 19 * * 1,4` — Mon+Thu 19:19 UTC)
and via `workflow_dispatch`, `timeout-minutes: 360`, with a
`~/.cache/huggingface` cache for the SigLIP model. Secrets required: `SUPABASE_URL`,
`SUPABASE_KEY` only. Artifacts: `logs/failed_products.log` and
`logs/last_run_summary.json` (if present).