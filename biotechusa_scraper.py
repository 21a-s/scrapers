"""
BioTechUSA Price Scraper
========================
Crawls every product on shop.biotechusa.de and outputs a CSV with:
  product name | size | price

Requirements (install once):
  pip install playwright
  playwright install chromium

Run:
  python biotechusa_scraper.py

Output: biotechusa_prices.csv  (same folder as this script)
"""

import asyncio
import csv
import re
import time
from playwright.async_api import async_playwright

# All supplement + accessory category URLs
CATEGORY_URLS = [
    "https://shop.biotechusa.de/collections/protein",
    "https://shop.biotechusa.de/collections/aminosauren",
    "https://shop.biotechusa.de/collections/vitamine",
    "https://shop.biotechusa.de/collections/collagen",
    "https://shop.biotechusa.de/collections/beauty-line",
    "https://shop.biotechusa.de/collections/gelenkschutz",
    "https://shop.biotechusa.de/collections/gewichtsmanagement-formeln",
    "https://shop.biotechusa.de/collections/ballaststoffreich",
    "https://shop.biotechusa.de/collections/krautererganzungen",
    "https://shop.biotechusa.de/collections/vitamine-aus-biologischen-quellen",
    "https://shop.biotechusa.de/collections/nahrungserganzungsmittel-fur-ausdauersportler",
    "https://shop.biotechusa.de/collections/creatine",
    "https://shop.biotechusa.de/collections/gainer",
    "https://shop.biotechusa.de/collections/kraft-und-leistungssteigerung",
    "https://shop.biotechusa.de/collections/testo-booster",
    "https://shop.biotechusa.de/collections/grundpulver-zum-kochen-und-backen",
    "https://shop.biotechusa.de/collections/proteinriegel-musliriegel-energieriegel",
    "https://shop.biotechusa.de/collections/proteinkremes-und-snacks",
    "https://shop.biotechusa.de/collections/sussstoffe",
    "https://shop.biotechusa.de/collections/shaker-trinkflaschen",
    "https://shop.biotechusa.de/collections/handschuhe",
    "https://shop.biotechusa.de/collections/gurtel",
    "https://shop.biotechusa.de/collections/zubehor-fur-das-training",
    "https://shop.biotechusa.de/collections/herrenbekleidung",
    "https://shop.biotechusa.de/collections/damenbekleidung",
    "https://shop.biotechusa.de/collections/seamless",
]

OUTPUT_FILE = "biotechusa_prices.csv"


def clean_price(text):
    """Extract price like '28,90€' from text."""
    m = re.search(r'\d+[,\.]\d+\s*€', text)
    return m.group(0).replace(' ', '') if m else ''


def clean_size(text):
    """Extract size/quantity like '454 g', '90 caps', etc."""
    m = re.search(r'\d[\d,\.]*\s*(g|kg|ml|l|caps|kaps|tbl|tab|tabletten|kapseln|pack|stk|cm|mm|ml\s*ampulle)', text, re.IGNORECASE)
    return m.group(0).strip() if m else text.strip()


async def get_all_product_urls(page, category_url):
    """Get all product URLs from a category, handling pagination."""
    urls = set()
    current = category_url

    while current:
        print(f"  → Fetching category page: {current}")
        try:
            await page.goto(current, wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(2000)  # let JS render
        except Exception as e:
            print(f"    ⚠ Error loading page: {e}")
            break

        # Collect all product links
        links = await page.eval_on_selector_all(
            'a[href*="/products/"]',
            'els => els.map(e => e.href)'
        )
        for link in links:
            # Only product pages, not collection-filtered ones
            if '/products/' in link and '?' not in link:
                urls.add(link.split('?')[0])

        # Check for next page link
        next_link = await page.query_selector('a[href*="?page="], a.next, [rel="next"]')
        if next_link:
            href = await next_link.get_attribute('href')
            if href and 'page=' in href:
                current = 'https://shop.biotechusa.de' + href if href.startswith('/') else href
            else:
                current = None
        else:
            # Try pattern-based next page
            page_match = re.search(r'\?page=(\d+)', current)
            # Check if a numbered next page link exists
            next_page_link = await page.query_selector(f'a[href*="page="]')
            if next_page_link:
                href = await next_page_link.get_attribute('href')
                current = 'https://shop.biotechusa.de' + href if href.startswith('/') else href
            else:
                current = None

    return urls


async def scrape_product(page, url):
    """Scrape a single product page — returns list of (name, size, price) tuples."""
    results = []
    try:
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(1500)

        # Get product title
        title_el = await page.query_selector('h1, .product__title, [class*="product-title"]')
        title = (await title_el.inner_text()).strip() if title_el else ''
        if not title:
            return results

        # Remove flavor from title — keep base product name only
        # Strip trailing flavor descriptors after last size indicator
        base_title = re.sub(r'\s*[-–]\s*(454|908|1816|2270|500|1000|2000|4000|28|25|30|300|360|700)\s*g.*$', '', title, flags=re.IGNORECASE).strip()
        base_title = re.sub(r'\s+(chocolate|vanilla|strawberry|banana|hazelnut|caramel|coconut|unflavoured|natur|ohne geschmack).*$', '', base_title, flags=re.IGNORECASE).strip()

        # Strategy 1: Look for size selector options with prices
        # BioTechUSA uses variant selectors
        variant_items = await page.query_selector_all('[class*="variant"], [class*="size-option"], select option, [data-value]')

        if not variant_items:
            # Strategy 2: Look for the price directly + size in title/description
            price_el = await page.query_selector('[class*="price"]:not([class*="compare"]), .price__regular, .product__price')
            price_text = (await price_el.inner_text()).strip() if price_el else ''
            price = clean_price(price_text)

            # Extract size from title
            size_m = re.search(r'(\d[\d,\.]*\s*(g|kg|ml|l|caps|kaps|tbl|tab|pack|stk))', title, re.IGNORECASE)
            size = size_m.group(0).strip() if size_m else ''

            if price:
                results.append((base_title, size, price))
        else:
            # Try to get all size variants with their prices
            # Check for a size dropdown
            size_select = await page.query_selector('select[name*="size"], select[name*="Packung"], select[id*="size"]')
            if size_select:
                options = await size_select.query_selector_all('option')
                for opt in options:
                    val = (await opt.get_attribute('value') or '').strip()
                    text = (await opt.inner_text()).strip()
                    if not val or val == '':
                        continue

                    # Select this option to get its price
                    await size_select.select_option(value=val)
                    await page.wait_for_timeout(800)

                    price_el = await page.query_selector('[class*="price"]:not([class*="compare"]):not([class*="was"])')
                    price_text = (await price_el.inner_text()).strip() if price_el else ''
                    price = clean_price(price_text)

                    size = clean_size(text or val)
                    if price and size:
                        results.append((base_title, size, price))
            else:
                # Fallback: just get the displayed price + size from title
                price_el = await page.query_selector('[class*="price"]:not([class*="compare"])')
                price_text = (await price_el.inner_text()).strip() if price_el else ''
                price = clean_price(price_text)
                size_m = re.search(r'(\d[\d,\.]*\s*(g|kg|ml|l|caps|kaps|tbl|tab|pack|stk))', title, re.IGNORECASE)
                size = size_m.group(0).strip() if size_m else ''
                if price:
                    results.append((base_title, size, price))

    except Exception as e:
        print(f"    ⚠ Error scraping {url}: {e}")

    return results


async def main():
    all_products = []
    seen_urls = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)  # set False to watch it work
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = await context.new_page()

        # Step 1: Collect all product URLs from all categories
        print("=" * 60)
        print("STEP 1: Collecting product URLs from all categories...")
        print("=" * 60)
        all_urls = set()
        for cat_url in CATEGORY_URLS:
            print(f"\nCategory: {cat_url.split('/')[-1]}")
            urls = await get_all_product_urls(page, cat_url)
            print(f"  Found {len(urls)} products")
            all_urls.update(urls)
            time.sleep(1)  # polite delay between categories

        print(f"\n✅ Total unique product URLs: {len(all_urls)}")

        # Step 2: Scrape each product page
        print("\n" + "=" * 60)
        print("STEP 2: Scraping each product page for sizes & prices...")
        print("=" * 60)

        for i, url in enumerate(sorted(all_urls), 1):
            if url in seen_urls:
                continue
            seen_urls.add(url)

            print(f"[{i}/{len(all_urls)}] {url.split('/')[-1]}")
            results = await scrape_product(page, url)
            for r in results:
                print(f"  ✓ {r[0]} | {r[1]} | {r[2]}")
            all_products.extend(results)

            # Polite delay to avoid rate limiting
            await page.wait_for_timeout(1200)

        await browser.close()

    # Step 3: Write CSV
    print("\n" + "=" * 60)
    print(f"STEP 3: Writing {len(all_products)} entries to {OUTPUT_FILE}...")
    print("=" * 60)

    # Deduplicate
    seen = set()
    unique = []
    for row in all_products:
        key = (row[0].lower().strip(), row[1].lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(row)

    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Produktname', 'Größe / Menge', 'Preis'])
        for row in sorted(unique, key=lambda x: x[0]):
            writer.writerow(row)

    print(f"\n✅ Done! Saved to: {OUTPUT_FILE}")
    print("   Upload this CSV to Claude to fill your order form.")


if __name__ == '__main__':
    asyncio.run(main())
