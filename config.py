"""Our Legacy scraper configuration."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    BRAND_NAME: str = "Our Legacy"
    SOURCE: str = "scraper-ourlegacy"
    BRAND_COLUMN: str = "Our Legacy"
    SECOND_HAND: bool = False
    LANDING_PAGE: str = "https://www.ourlegacy.com"
    BASE_URL: str = "https://www.ourlegacy.com"
    CURRENCY: str = "EUR"
    COUNTRY: str = None

    SITEMAP_INDEX_URL: str = "https://www.ourlegacy.com/sitemap/sitemap.xml"

    CATEGORY_MAP: dict[str, str] = field(default_factory=lambda: {
        "mens/jeans": "Jeans",
        "mens/jersey": "Jersey",
        "mens/knitwear": "Knitwear",
        "mens/leather": "Leather",
        "mens/outerwear": "Outerwear",
        "mens/outerwear/coats": "Coats",
        "mens/outerwear/jackets": "Jackets",
        "mens/outerwear/overshirts": "Overshirts",
        "mens/outerwear/parkas": "Parkas",
        "mens/outerwear/vests": "Vests",
        "mens/shirting": "Shirting",
        "mens/shorts": "Shorts",
        "mens/suiting": "Suiting",
        "mens/trousers": "Trousers",
        "womens/denim": "Denim",
        "womens/dresses": "Dresses",
        "womens/jersey": "Jersey",
        "womens/knitwear": "Knitwear",
        "womens/leather": "Leather",
        "womens/outerwear": "Outerwear",
        "womens/shirting": "Shirting",
        "womens/shorts": "Shorts",
        "womens/skirts": "Skirts",
        "womens/suiting": "Suiting",
        "womens/swimwear": "Swimwear",
        "womens/tops": "Tops",
        "womens/trousers": "Trousers",
        "footwear/mens-footwear": "Footwear",
        "footwear/mens-footwear/mens-boots": "Boots",
        "footwear/mens-footwear/mens-mules": "Mules",
        "footwear/mens-footwear/mens-shoes": "Shoes",
        "footwear/mens-footwear/mens-sneakers": "Sneakers",
        "footwear/womens-footwear": "Footwear",
        "footwear/womens-footwear/womens-boots": "Boots",
        "footwear/womens-footwear/womens-mules": "Mules",
        "footwear/womens-footwear/womens-shoes": "Shoes",
        "footwear/womens-footwear/womens-sneakers": "Sneakers",
        "accessories/bags": "Bags",
        "accessories/belts": "Belts",
        "accessories/crochet-bags": "Bags",
        "accessories/eyewear": "Eyewear",
        "accessories/gloves": "Gloves",
        "accessories/hats": "Hats",
        "accessories/jewelery": "Jewelry",
        "accessories/ladons": "Bags",
        "accessories/other": "Accessories",
        "accessories/scarves": "Scarves",
        "accessories/small-leather-goods": "Small Leather Goods",
        "accessories/ties": "Ties",
        "workshop/dickies": "Workshop",
        "workshop/sport": "Workshop",
        "workshop/work-shop": "Workshop",
    })

    SUPABASE_URL: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    SUPABASE_KEY: str = field(default_factory=lambda: os.getenv("SUPABASE_KEY", ""))

    EMBEDDING_MODEL: str = "google/siglip-base-patch16-384"
    EMBEDDING_DIM: int = 768
    EMBEDDING_VERSION: int = 2
    RATE_LIMIT_DELAY: float = 0.5
    BATCH_SIZE: int = 5
    STALE_MISS_THRESHOLD: int = 2
    REQUEST_TIMEOUT: int = 30
    CONCURRENCY: int = 6
    USER_AGENT: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    GENDER_DEFAULT: str = "Unisex"

    # Price map: Centra market IDs
    PRICE_MARKET_EUR: int = 3
    PRICE_MARKET_USD: int = 23
    PRICE_MARKET_GBP: int = 7


cfg = Config()
