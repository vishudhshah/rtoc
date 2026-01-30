import os
import json
import asyncio
import re
import time
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from ebooklib import epub

BASE_URL = "https://wetriedtls.com"
SERIES_URL = f"{BASE_URL}/series/a-regressors-tale-of-cultivation"
DATA_DIR = "data"
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")
CHAPTERS_FILE = os.path.join(DATA_DIR, "chapters.json")
OUTPUT_EPUB = "A_Regressors_Tale_of_Cultivation.epub"
CONCURRENCY_LIMIT = 10  # Number of parallel pages
MAX_RETRIES = 3

def ensure_dirs():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

def load_json(filepath):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

async def handle_popup(page):
    try:
        button = page.get_by_role("button", name="I understand", exact=False)
        if await button.is_visible():
            await button.click()
            print("Clicked 'I understand' popup.")
    except Exception:
        pass

async def scrape_metadata_async(max_pages=40):
    print("Scraping series metadata (all pages)...")
    metadata = {}
    ordered_slugs = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(SERIES_URL)
        
        await handle_popup(page)
        
        # Click on "Chapters list" tab
        try:
            tab = page.locator("button, a, span").filter(has_text=re.compile(r"Chapters list", re.I)).first
            if await tab.is_visible():
                await tab.click()
                print("Clicked 'Chapters list' tab.")
                # Specific selector to ensure we are waiting for the actual list content
                list_selector = 'div[role="tabpanel"][id*="-content-chapters_list"] a[href*="/series/a-regressors-tale-of-cultivation/"]'
                await page.wait_for_selector(list_selector, timeout=20000)
                # Small safety sleep to allow more links to populate
                await asyncio.sleep(1)
        except Exception as e:
            print(f"Warning: Could not click chapters list tab or wait for content: {e}")

        async def extract_current_page():
            # Ensure we only look inside the active chapters list container
            list_container = page.locator('div[role="tabpanel"][id*="-content-chapters_list"]')
            content = await list_container.inner_html()
            soup = BeautifulSoup(content, 'html.parser')
            links_found = 0
            
            # Find all potential chapter links in this specific container
            all_links = soup.find_all('a', href=re.compile(r'/series/a-regressors-tale-of-cultivation/'))
            
            for link in all_links:
                href = link.get('href')
                slug = href.split('/')[-1]
                if not slug or slug == 'a-regressors-tale-of-cultivation':
                    continue
                
                # Filter out 'Paid' chapters
                parent_text = link.find_parent('li').get_text() if link.find_parent('li') else ""
                if "Paid" in parent_text:
                    continue

                if slug not in metadata:
                    temp_link = BeautifulSoup(str(link), 'html.parser').find('a')
                    date_span = temp_link.find('span', class_=re.compile(r'text-muted-foreground.*text-\[10px\]'))
                    release_date = "Unknown"
                    if date_span:
                        release_date = date_span.get_text(strip=True)
                        date_span.decompose()
                    
                    # Collect unique text from spans to avoid duplication (e.g. Author's Q&A (12))
                    span_texts = []
                    for s in temp_link.find_all('span'):
                        txt = s.get_text(strip=True)
                        if txt and txt not in span_texts:
                            span_texts.append(txt)
                    
                    clean_title = " ".join(span_texts)
                    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
                    
                    metadata[slug] = {
                        "url": BASE_URL + href if href.startswith('/') else href,
                        "title": clean_title,
                        "release_date": release_date,
                        "slug": slug
                    }
                    ordered_slugs.append(slug)
                    links_found += 1
            return links_found

        for p_idx in range(1, max_pages + 1):
            print(f"Scraping metadata page {p_idx}...")
            new_links = await extract_current_page()
            print(f"Found {new_links} new chapter links on page {p_idx}.")
            
            try:
                next_page_num = str(p_idx + 1)
                next_button = page.locator("li a, li button").filter(has_text=re.compile(f"^{next_page_num}$")).first
                if await next_button.is_visible():
                    await next_button.click()
                    await asyncio.sleep(2)
                else:
                    next_button = page.locator("li a, li button").filter(has_text=re.compile(r"^>$|Next", re.I)).first
                    if await next_button.is_visible():
                        await next_button.click()
                        await asyncio.sleep(2)
                    else:
                        print(f"No more pagination buttons found at page {p_idx}.")
                        break
            except Exception as e:
                print(f"Error navigating to next page: {e}")
                break
                
        await browser.close()
        
    # Website is descending (latest first). Reverse to get release order (oldest first).
    ordered_slugs.reverse()
    
    return {"metadata": metadata, "order": ordered_slugs}

async def scrape_chapter_content_async(context, url, slug, meta_title=None):
    for attempt in range(MAX_RETRIES):
        page = await context.new_page()
        try:
            if attempt == 0:
                print(f"Scraping {slug}")
            else:
                print(f"Retrying {slug} (Attempt {attempt + 1})")
            timeout = 30000 + (attempt * 10000)
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            await handle_popup(page)
            
            try:
                await page.wait_for_selector("#reader-container", timeout=10000)
            except Exception:
                await page.close()
                continue
                
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            container = soup.find(id="reader-container")
            
            if not container:
                await page.close()
                continue
                
            p_tags = container.find_all('p')
            ad_keywords = ["Discord", "Ko-fi", "Patreon", "Want more chapters", "Next chapter", "Previous chapter", "Consider supporting"]
            
            title_pattern = ""
            if slug == "chapter-0":
                title_pattern = "Prologue"
            elif slug.startswith("chapter-"):
                ch_num = slug.split("-")[-1]
                if ch_num.isdigit():
                    title_pattern = f"Chapter {ch_num}"
            
            cleaned_p_tags = []
            for p in p_tags:
                text = p.get_text(" ", strip=True)
                is_title_p = (title_pattern and title_pattern in text) or (meta_title and meta_title in text)
                
                if slug == "chapter-807-808":
                    if "Chapter 807" in text or "Chapter 808" in text or "Afterword" in text:
                        is_title_p = True

                if any(kw in text for kw in ad_keywords) and not is_title_p:
                    continue
                
                if not text:
                    continue
                                    
                cleaned_p_tags.append(p)

            await page.close()

            if slug == "chapter-807-808":
                ch807_content, ch808_content = [], []
                title807, title808 = "Chapter 807", "Chapter 808"
                current_ch = 807
                for p in cleaned_p_tags:
                    text_with_newlines = p.get_text("\n", strip=True)
                    lines = [l.strip() for l in text_with_newlines.split("\n") if l.strip()]
                    
                    found_807_in_p = False
                    found_808_in_p = False
                    
                    for line in lines:
                        match807 = re.search(r'Chapter 807[:\s\-].*$', line, re.I)
                        match808 = re.search(r'(Chapter 808[:\s\-].*|(?<!\w)Afterword[:\s\-].*)$', line, re.I)
                        
                        if match807:
                            title807 = match807.group(0).strip()
                            found_807_in_p = True
                        if match808:
                            current_ch = 808
                            title808 = match808.group(0).strip()
                            found_808_in_p = True
                    
                    if current_ch == 807: ch807_content.append(str(p))
                    else: ch808_content.append(str(p))
                
                return {
                    "chapter-807": {"content": "\n".join(ch807_content), "title": title807, "source_slug": slug},
                    "chapter-808": {"content": "\n".join(ch808_content), "title": title808, "source_slug": slug}
                }
            
            # Normal title extraction
            title = meta_title if meta_title else slug
            
            # Prioritize finding a detailed title in the content
            # Most chapters start with "Chapter X: [Title]"
            found_title = False
            title_search_pattern = title_pattern if title_pattern else r"(Chapter \d+|Author's Q&A \(\d+\)|Author's Tidbit \(\d+\))"
            
            for p in cleaned_p_tags[:5]:
                text_with_newlines = p.get_text("\n", strip=True)
                lines = [l.strip() for l in text_with_newlines.split("\n") if l.strip()]
                
                for line in lines:
                    # Look for "Chapter X: ..." or similar. Spacer is optional.
                    # This allows matching "Prologue" or "Chapter 1" exactly.
                    match = re.search(rf'^({title_search_pattern}([:\s\-].*)?)$', line, re.I)
                    if not match and title_pattern: 
                        match = re.search(rf'^({title_pattern}(\s+.*)?)$', line, re.I)
                    
                    if match:
                        potential_title = match.group(1).strip()
                        # Clean the potential title
                        for kw in ad_keywords:
                            if kw in potential_title:
                                potential_title = potential_title.split(kw)[0].strip()
                        
                        # Prioritize the content title if it's descriptive enough, 
                        # or if the current title is likely just a merger from metadata.
                        if len(potential_title) >= 8 or slug == "chapter-0":
                            title = potential_title
                            found_title = True
                            break
                if found_title: break
            
            return {slug: {"content": "\n".join([str(p) for p in cleaned_p_tags]), "title": title}}

        except PlaywrightTimeoutError:
            print(f"Timeout on chapter {slug}, attempt {attempt + 1}")
            await page.close()
        except Exception as e:
            print(f"Error on chapter {slug}: {e}")
            await page.close()
            
    return None

async def worker(context, queue, chapters_data, semaphore):
    while True:
        item = await queue.get()
        url, slug, meta_title = item
        async with semaphore:
            result = await scrape_chapter_content_async(context, url, slug, meta_title)
            if result:
                chapters_data.update(result)
                save_json(CHAPTERS_FILE, chapters_data)
            else:
                print(f"Failed to scrape {slug} after retries.")
        queue.task_done()

def create_epub(metadata_obj, chapters_data):
    print("Generating EPUB...")
    metadata = metadata_obj.get("metadata", {})
    ordered_slugs = metadata_obj.get("order", [])
    
    book = epub.EpubBook()
    book.set_identifier("rtoc-scraper-002")
    book.set_title("A Regressor’s Tale of Cultivation")
    book.set_language("en")
    book.add_author("엄청난 (Tremendous)")

    style = 'p { margin-bottom: 1.2em; line-height: 1.5; } h1 { text-align: center; } .date { text-align: center; font-style: italic; color: #666; margin-bottom: 2em; }'
    nav_css = epub.EpubItem(uid="style_nav", file_name="style/nav.css", media_type="text/css", content=style)
    book.add_item(nav_css)

    chapters = []
    
    for slug in ordered_slugs:
        # Special case: 807-808 page needs to map to two entries
        target_slugs = [slug]
        if slug == "chapter-807-808":
            target_slugs = ["chapter-807", "chapter-808"]
            
        for t_slug in target_slugs:
            if t_slug not in chapters_data:
                continue
                
            ch_info = chapters_data[t_slug]
            meta = metadata.get(slug, {}) # Always use the source slug for meta
            release_date = meta.get("release_date", "Unknown")
            title = ch_info.get("title", meta.get("title", t_slug))
            content = ch_info.get("content", "")

            # Sanitize file name
            safe_slug = re.sub(r'[^a-zA-Z0-9-]', '_', t_slug)
            ch_html = epub.EpubHtml(title=title, file_name=f'{safe_slug}.xhtml', lang='en')
            ch_html.content = f'<h1>{title}</h1><div class="date">Released: {release_date}</div>{content}'
            ch_html.add_item(nav_css)
            book.add_item(ch_html)
            chapters.append(ch_html)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters
    epub.write_epub(OUTPUT_EPUB, book, {})
    print(f"EPUB generated successfully: {OUTPUT_EPUB}")

async def main(limit_indices=None, force_rebuild=False):
    ensure_dirs()
    metadata_obj = load_json(METADATA_FILE)
    if not metadata_obj or "order" not in metadata_obj:
        metadata_obj = await scrape_metadata_async()
        if metadata_obj: save_json(METADATA_FILE, metadata_obj)
        else: return

    metadata = metadata_obj.get("metadata", {})
    ordered_slugs = metadata_obj.get("order", [])
    
    chapters_data = load_json(CHAPTERS_FILE)
    
    queue = asyncio.Queue()
    for idx, slug in enumerate(ordered_slugs):
        if limit_indices and idx not in limit_indices:
            continue
            
        meta = metadata[slug]
        
        # Check if already scraped
        already_scraped = False
        if slug == "chapter-807-808":
            if "chapter-807" in chapters_data and "chapter-808" in chapters_data:
                already_scraped = True
        elif slug in chapters_data:
            # Check if title is just the slug (indicates retry might be needed or meta was better)
            ch_data = chapters_data[slug]
            if ch_data.get("title") == slug and slug.startswith("chapter-"):
                already_scraped = False
            else:
                already_scraped = True
        
        if already_scraped and not force_rebuild:
            continue
            
        await queue.put((meta['url'], slug, meta.get('title')))

    if queue.empty():
        print("No new/missing chapters to scrape.")
    else:
        print(f"Starting scraping of {queue.qsize()} items...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
            
            tasks = []
            for _ in range(CONCURRENCY_LIMIT):
                tasks.append(asyncio.create_task(worker(context, queue, chapters_data, semaphore)))

            await queue.join()
            for task in tasks: task.cancel()
            await browser.close()

    print("Scraping complete.")
    if chapters_data:
        create_epub(metadata_obj, chapters_data)

if __name__ == "__main__":
    import sys
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        force = "--force" in sys.argv
        args = [a for a in sys.argv[1:] if a != "--force"]
        if args:
            # If numbers are provided, treat them as indices in the ordered list for targeted testing
            test_limit = [int(v) for v in args if v.isdigit()]
            loop.run_until_complete(main(limit_indices=test_limit, force_rebuild=force))
        else:
            loop.run_until_complete(main(force_rebuild=force))
    except KeyboardInterrupt:
        print("\nInterrupted.")
