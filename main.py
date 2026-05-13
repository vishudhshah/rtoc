import io
import os
import json
import asyncio
import re
import contextlib
import smartypants
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from ebooklib import epub
import httpx
from fontTools import subset as ft_subset
from fontTools.ttLib import TTFont

BASE_URL = "https://wetriedtls.com"
SERIES_URL = f"{BASE_URL}/series/a-regressors-tale-of-cultivation"
DATA_DIR = "data"
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")
CHAPTERS_FILE = os.path.join(DATA_DIR, "chapters.json")
OUTPUT_EPUB = "A_Regressors_Tale_of_Cultivation.epub"
CONCURRENCY_LIMIT = 10  # Adjust based on system resources
MAX_RETRIES = 3

DESCRIPTION = """On the way to a company workshop, we fell into a world of immortal cultivators while still in the car.

Those with spiritual roots and unique abilities were all called to join cultivation sects, living prosperously.

But I, having neither spiritual roots nor special abilities, lived as an ordinary mortal for 50 years, accepting my fate until my death.

That’s what I thought.

Until I regressed."""

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

async def handle_popup(page, wait_for_visible=False):
    try:
        # Wait a moment for dynamic content or popups to appear
        # The 'I understand' button might take a second to render
        button = page.locator("button").filter(has_text=re.compile(r"I understand", re.I)).first
        
        if wait_for_visible:
            await button.wait_for(state="visible", timeout=5000)
            print("Found 'I understand' popup. Clicking...")
            await button.click()
            # Wait for the popup to disappear
            await button.wait_for(state="hidden", timeout=5000)
            print("Popup dismissed.")
        elif await button.is_visible():
            await button.click()
            print("Clicked 'I understand' popup.")
    except Exception:
        pass

async def generate_metadata_async(max_pages=40, existing_metadata=None, force_full_scan=False):
    if existing_metadata is None:
        existing_metadata = {}
    print("Checking for new chapters...")
    metadata = existing_metadata.get("metadata", {}).copy()
    ordered_slugs = existing_metadata.get("order", []).copy()
    
    # Track new chapters found
    new_slugs = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(SERIES_URL)
        
        await handle_popup(page, wait_for_visible=True)
        
        # Extract cover image URL
        cover_image_url = None
        try:
            # Wait for any image that might be the cover
            await page.wait_for_selector('img.rounded', timeout=10000)
            # Find the main cover image
            # Based on browser subagent: div.lg:col-span-3 div > img.rounded
            img_element = page.locator('div.lg\\:col-span-3 div > img.rounded').first
            if await img_element.is_visible():
                cover_image_url = await img_element.get_attribute('src')
                # If it's a next/image URL, try to get the original if possible, or just use it
                if cover_image_url and "_next/image?url=" in cover_image_url:
                    match = re.search(r'url=([^&]+)', cover_image_url)
                    if match:
                        from urllib.parse import unquote
                        cover_image_url = unquote(match.group(1))
        except Exception as e:
            print(f"Warning: Could not find cover image: {e}")

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
            # Use evaluate to extract data directly from the DOM, which is more robust than inner_html+BS4
            # especially for dynamic frameworks like Next.js
            chapters_data = await page.evaluate('''() => {
                const container = document.querySelector('div[role="tabpanel"][id*="-content-chapters_list"]');
                if (!container) return [];
                
                const links = Array.from(container.querySelectorAll('a[href*="/series/a-regressors-tale-of-cultivation/"]'));
                return links.map(link => {
                    const href = link.getAttribute('href');
                    const text = link.innerText;
                    // Check for "Paid" indicator - usually in a span or sibling text
                    // Based on HTML structure, the link wraps the li.
                    // We check the entire text content of the link for "Paid" or "Locked" keywords if necessary,
                    // but usually paid chapters have a lock icon or specific text.
                    // Checking parent or structure can be done here.
                    
                    // Simple check on text
                    const isPaid = text.includes("Paid") || text.includes("Locked");
                    
                    return { href, text, isPaid };
                });
            }''')

            links_found = 0
            all_already_known = True
            
            for item in chapters_data:
                href = item['href']
                slug = href.split('/')[-1]
                
                if not slug or slug == 'a-regressors-tale-of-cultivation':
                    continue
                
                if item['isPaid']:
                    continue
                
                # Check for existing metadata first to prevent duplicates
                # if slug in metadata:
                #    continue
                
                # Clean title and parse date (simplified from original BS4 logic)
                raw_text = item['text']
                # Extract title
                # Usually "Chapter X ... \n ... date"
                lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
                clean_title = slug
                
                # Check for "Chapter X" in the text to use as base, instead of slug
                # This handles cases where slug is "chapter-211" but title text is "Chapter 211"
                if lines:
                    possible_header = lines[0]
                    if possible_header.lower().replace(" ", "-") == slug.lower() or "chapter" in possible_header.lower():
                         clean_title = possible_header

                release_date = "Unknown"
                
                if lines:
                    clean_title = lines[0] # First line is usually the title
                    if len(lines) > 1:
                        # Try to find date in subsequent lines
                        for i, line in enumerate(lines[1:], 1):
                            # Simple heuristic for date
                            if re.search(r'\d{1,2}/\d{1,2}/\d{4}|\d+\s+days?\s+ago|yesterday|today', line, re.I):
                                release_date = line
                                # Date found. Check if the line BEFORE it was a subtitle.
                                if i == 2:
                                    potential_subtitle = lines[1].strip()
                                    # Filter out common non-title badges
                                    bad_badges = ["Spoiler", "Paid", "Locked", "New"]
                                    if potential_subtitle and not any(x in potential_subtitle for x in bad_badges):
                                         # Improved subtitle logic
                                         # 1. If subtitle contains the full title (e.g. Title="Author's Q&A", Sub="Author's Q&A (1)"), use subtitle.
                                         if clean_title in potential_subtitle:
                                             clean_title = potential_subtitle
                                         # 2. If title contains subtitle, do nothing.
                                         elif potential_subtitle in clean_title:
                                             pass
                                         # 3. Otherwise append if not already present
                                         elif ":" not in clean_title and not clean_title.endswith(potential_subtitle):
                                             clean_title = f"{clean_title}: {potential_subtitle}"
                                break
                
                # Check for existing metadata
                if slug in metadata:
                    # Check if we should update the title
                    existing_title = metadata[slug].get("title", "")
                    
                    # Update if new title has a subtitle (contains :) and old one didn't,
                    # or if new title is significantly longer and contains the old title.
                    should_update = False
                    if ":" in clean_title and ":" not in existing_title:
                        should_update = True
                    elif len(clean_title) > len(existing_title) + 5 and existing_title in clean_title:
                        should_update = True
                        
                    if should_update:
                        print(f"Updating metadata for {slug}: '{existing_title}' -> '{clean_title}'")
                        metadata[slug]["title"] = clean_title
                        if release_date != "Unknown":
                            metadata[slug]["release_date"] = release_date
                        all_already_known = False  # treat as "new" to keep scanning
                    
                    continue

                if slug not in metadata:
                    all_already_known = False
                    new_slugs.append(slug)

                metadata[slug] = {
                    "url": BASE_URL + href if href.startswith('/') else href,
                    "title": clean_title,
                    "release_date": release_date,
                    "slug": slug
                }
                links_found += 1
            
            # Return the first slug found to help detect page changes
            first_slug = None
            for item in chapters_data:
                slug = item['href'].split('/')[-1]
                if slug and slug != 'a-regressors-tale-of-cultivation':
                    first_slug = slug
                    break

            page_valid_links_count = 0
            all_known_on_this_page = True
            
            for item in chapters_data:
                 slug = item['href'].split('/')[-1]
                 if slug and slug != 'a-regressors-tale-of-cultivation' and not item['isPaid']:
                     page_valid_links_count += 1
                     # If this slug WAS added to new_slugs just now, it means it wasn't known before
                     if slug in new_slugs: 
                         all_known_on_this_page = False
                     
            return page_valid_links_count, all_known_on_this_page, first_slug

        current_page_first_slug = None
        
        for p_idx in range(1, max_pages + 1):
            print(f"Generating metadata page {p_idx}...")
            
            # If we just navigated, ensure the content has actually changed
            retries = 0
            while True:
                new_links, all_known, first_slug = await extract_current_page()
                
                # If we have a previous slug to compare against, and they are the same,
                # it means the page hasn't updated yet.
                if current_page_first_slug and first_slug == current_page_first_slug and retries < 5:
                    print(f"Page content hasn't changed yet, waiting... ({retries+1}/5)")
                    await asyncio.sleep(2)
                    retries += 1
                    continue
                break
            
            current_page_first_slug = first_slug

            if new_links > 0:
                print(f"Found {new_links} chapter links on page {p_idx}")
            
            if all_known and not force_full_scan and new_links > 0:
                print("All chapters on this page are already known. Stopping metadata scan.")
                break
            
            # If we found 0 links on the first page, something is wrong (likely blocked)
            if new_links == 0 and p_idx == 1:
                print("Warning: No chapters found on textual scan of page 1. The page might be loading or blocked.")
                # We don't break here to allow trying pagination or seeing if content loads late
            elif new_links == 0 and all_known:
                 # Standard end of list (empty page at end)
                 print("No more chapters found. Stopping.")
                 break

            try:
                next_page_num = str(p_idx + 1)
                # Broader selector for pagination numbers
                next_button = page.locator("li a, li button, button").filter(has_text=re.compile(f"^{next_page_num}$")).first
                if await next_button.is_visible():
                    await next_button.click()
                    # Initial wait for click to register
                    await asyncio.sleep(1)
                else:
                    # Broader selector for Next button
                    next_button = page.locator("li a, li button, button, a").filter(has_text=re.compile(r"^>$|Next", re.I)).first
                    if await next_button.is_visible():
                        await next_button.click()
                        await asyncio.sleep(1)
                    else:
                        print(f"No more pagination buttons found at page {p_idx}.")
                        break
            except Exception as e:
                print(f"Error navigating to next page: {e}")
                # Try to handle popup in case it appeared late
                await handle_popup(page, wait_for_visible=True)
                break
                
        await browser.close()
        
    # Websites often list newest first. Chapter order should be oldest first for the EPUB.
    # We prepended new ones (descending), so we need to reverse them and extend the old list.
    if new_slugs:
        new_slugs.reverse()
        ordered_slugs.extend(new_slugs)
    
    return {"metadata": metadata, "order": ordered_slugs, "cover_image_url": cover_image_url}

def clean_text_node_content(text):
    # 1. Handle mixed/double-single quotes -> double quotes
    # The user requested 'double single quotes' be fixed to double quotes.
    # We treat mixed artifacts (like ‘' or ’' or ‘' etc) as double quotes too.
    text = text.replace("‘'", '"').replace("'‘", '"')
    text = text.replace("’'", '"').replace("'’", '"')
    text = text.replace("‘‘", '"').replace("’’", '"')
    text = text.replace("''", '"')

    # 2. Normalize remaining smart quotes to straight quotes to ensure clean slate
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('‘', "'").replace('’', "'")
    return text

def apply_smartypants(text):
    if smartypants:
        # q=quotes, d=dashes, e=ellipses, u=unicode (no HTML entities)
        # We process the final HTML string, so skipping entities is safer/cleaner.
        attr = smartypants.Attr.q | smartypants.Attr.d | smartypants.Attr.e | smartypants.Attr.u
        return smartypants.smartypants(text, attr=attr)
    return text

# Matches a </p> or </hN> immediately followed (with optional whitespace) by a <p> or <hN> open tag.
# Used to split bracket content at paragraph boundaries for valid HTML wrapping.
_BRACKET_PARA_BOUNDARY = re.compile(
    r'(</(?:p|h[1-6])[^>]*>\s*<(?:p|h[1-6])[^>]*>)',
    re.I | re.DOTALL
)

def _format_bracket_span(content, is_inner):
    """
    Wrap bracket content in bold (outer) or bold+italic (inner) tags.
    When the content spans paragraph boundaries, each paragraph segment is
    wrapped individually to maintain valid HTML.
    """
    if is_inner:
        first_open = '<strong><em>['
        mid_open   = '<strong><em>'
        mid_close  = '</em></strong>'
        close      = ']</em></strong>'
    else:
        first_open = '<strong>['
        mid_open   = '<strong>'
        mid_close  = '</strong>'
        close      = ']</strong>'

    segments = _BRACKET_PARA_BOUNDARY.split(content)

    if len(segments) == 1:
        return f'{first_open}{content}{close}'

    result = []
    for i, seg in enumerate(segments):
        if i % 2 == 0:  # content segment
            if i == 0:
                result.append(f'{first_open}{seg}{mid_close}')
            elif i == len(segments) - 1:
                result.append(f'{mid_open}{seg}{close}')
            else:
                result.append(f'{mid_open}{seg}{mid_close}')
        else:  # paragraph boundary tag (</p>...<p>)
            result.append(seg)

    return ''.join(result)


def apply_bracket_formatting(text):
    """
    Apply square-bracket formatting to HTML content:
      [text]              → <strong>[text]</strong>
      [outer [inner] txt] → outer bold, inner bold+italic
      [para1            ] spanning multiple <p> tags → each paragraph segment
                            wrapped individually to stay valid HTML
    Unmatched brackets are left as-is.
    """
    parts = []   # accumulated output segments
    stack = []   # indices into `parts` of opening-bracket placeholders

    i = 0
    while i < len(text):
        if text[i] == '<':
            # Consume the entire HTML tag literally — don't process brackets inside attributes
            j = i + 1
            while j < len(text) and text[j] != '>':
                j += 1
            parts.append(text[i:j + 1])
            i = j + 1
        elif text[i] == '[':
            stack.append(len(parts))
            parts.append(None)   # placeholder; becomes '[' if never closed
            i += 1
        elif text[i] == ']' and stack:
            open_idx = stack.pop()
            # Collect everything written since the matching '['
            content = ''.join(p if p is not None else '[' for p in parts[open_idx + 1:])
            parts = parts[:open_idx]
            is_inner = len(stack) > 0
            parts.append(_format_bracket_span(content, is_inner))
            i += 1
        else:
            # Consume a run of non-bracket, non-tag characters in one slice
            j = i + 1
            while j < len(text) and text[j] not in '[]<':
                j += 1
            parts.append(text[i:j])
            i = j

    return ''.join(p if p is not None else '[' for p in parts)


def format_html_content(text):
    # Italicize single quoted sentences
    # Pattern looks for single quotes wrapping content.
    # Inner content allows:
    # 1. Any non-quote non-tag-start character: [^'<]
    # 2. An apostrophe that is sandwiched between word characters (e.g. don't, it's): (?<=\w)'(?=\w)
    pattern_italic = r"(?<=[ >\n])'((?:[^'<]|(?<=\w)'(?=\w))+?)'(?=[ <.,;:!?\n])"

    # Use a callback to wrap in em tags
    text = re.sub(pattern_italic, r"<em>'\1'</em>", text)

    # Bold (and nested bold+italic) square-bracketed content, cross-paragraph aware
    text = apply_bracket_formatting(text)

    return text

async def generate_chapter_content_async(context, url, slug, meta_title=None):
    for attempt in range(MAX_RETRIES):
        page = await context.new_page()
        try:
            if attempt == 0:
                print(f"Generating {slug}")
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
                
            p_tags = container.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr'])
            ad_keywords = ["Discord", "Ko-fi", "Patreon", "Want more chapters", "Next chapter", "Previous chapter", "Consider supporting", "buymeacoffee", "TranslatingNovice", "Z0Rel", "BlueMangoAde", "wetriedtls"]
            
            title_pattern = ""
            if slug == "chapter-0":
                title_pattern = "Prologue"
            elif slug.startswith("chapter-"):
                ch_num = slug.split("-")[-1]
                if ch_num.isdigit():
                    title_pattern = f"Chapter {ch_num}"
            
            # Extract title BEFORE filtering (to ensure we capture it even if the line has ads)
            title = meta_title if meta_title else slug
            found_title = False
            title_search_pattern = title_pattern if title_pattern else r"(Chapter \d+|Author's Q&A \(\d+\)|Author's Tidbit \(\d+\))"
            
            # Look for title in the first few raw paragraphs
            for p in p_tags[:10]:
                text_with_newlines = p.get_text("\n", strip=True)
                lines = [l.strip() for l in text_with_newlines.split("\n") if l.strip()]
                
                for line in lines:
                    match = re.search(rf'^({title_search_pattern}([:\s\-].*)?)$', line, re.I)
                    if not match and title_pattern:
                        match = re.search(rf'^({title_pattern}(\s+.*)?)$', line, re.I)

                    if not match:
                        match = re.search(r'^(Chapter \d+([:\s\-].*)?)$', line, re.I)

                    if match:
                        potential_title = match.group(1).strip()
                        # Clean the potential title from ads if they are inextricably linked (rare in regex mismatch but possible)
                        # We use the raw text for detection, but we want a clean title string.
                        for kw in ad_keywords:
                            if kw in potential_title:
                                potential_title = potential_title.split(kw)[0].strip()
                        
                        if len(potential_title) >= 8 or slug == "chapter-0":
                            title = potential_title
                            found_title = True
                            break
                if found_title: break
            
            cleaned_p_tags = []
            for p in p_tags:
                text = p.get_text(" ", strip=True)
                
                # Check for critical split markers for the 807-808 case
                is_split_marker = False
                if slug == "chapter-807-808":
                    if "Chapter 807" in text or "Chapter 808" in text or "Afterword" in text:
                        is_split_marker = True

                # If it contains an ad keyword, remove it, UNLESS it's a critical split marker
                if any(kw.lower() in text.lower() for kw in ad_keywords) and not is_split_marker:
                    continue
                
                if not text and p.name != 'hr':
                    continue
                                    
                cleaned_p_tags.append(p)

            # Apply textual cleanup to the valid tags in-place
            for p in cleaned_p_tags:
                for text_node in p.find_all(string=True, recursive=True):
                    cleaned_text = clean_text_node_content(str(text_node))
                    if cleaned_text != str(text_node):
                        text_node.replace_with(cleaned_text)

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
                        # Fix: Anchor Afterword to start of line to avoid matching usage in sentences
                        match808 = re.search(r'(Chapter 808[:\s\-].*|^Afterword(?:[:\s\.\-].*)?)$', line, re.I)
                        
                        if match807:
                            title807 = match807.group(0).strip()
                            found_807_in_p = True
                        if match808:
                            current_ch = 808
                            title808 = match808.group(0).strip()
                            found_808_in_p = True
                    
                    if current_ch == 807: 
                        if not found_807_in_p:
                            ch807_content.append(str(p))
                    else: 
                        if not found_808_in_p:
                            ch808_content.append(str(p))
                
                content807 = apply_smartypants(format_html_content("\n".join(ch807_content)))
                content808 = apply_smartypants(format_html_content("\n".join(ch808_content)))
                
                return {
                    "chapter-807": {"content": content807, "title": title807, "source_slug": slug},
                    "chapter-808": {"content": content808, "title": title808, "source_slug": slug}
                }
            
            # Remove the first paragraph if it is identical to the title
            # This logic happens AFTER title extraction, so we don't break title detection.
            if cleaned_p_tags:
                first_p_text = cleaned_p_tags[0].get_text(" ", strip=True)
                # Clean up the texts for comparison (remove smart quotes or extra spaces if any)
                clean_first_p = clean_text_node_content(first_p_text).replace('"', '').replace("'", "").lower().strip()
                clean_title = clean_text_node_content(title).replace('"', '').replace("'", "").lower().strip()
                
                # Check for exact match or if title is "Chapter X: Title" and first line is "Title" etc
                # Or if first line is "Chapter X" and title is "Chapter X"
                if clean_first_p == clean_title or clean_title in clean_first_p:
                    # Remove the first paragraph
                    cleaned_p_tags.pop(0)

            # Serialize first to get plain HTML with straight quotes
            final_content = "\n".join([str(p) for p in cleaned_p_tags])
            
            # Apply italicization (looks for straight '...')
            final_content = format_html_content(final_content)
            
            # Apply smart quotes (converts straight quotes to curly, preserving tags)
            final_content = apply_smartypants(final_content)
            
            return {slug: {"content": final_content, "title": title}}

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
            result = await generate_chapter_content_async(context, url, slug, meta_title)
            if result:
                chapters_data.update(result)
                save_json(CHAPTERS_FILE, chapters_data)
            else:
                print(f"Failed to generate {slug} after retries.")
        queue.task_done()

def collect_unicode_chars(book):
    """Return a set of all Unicode codepoints used across all XHTML items in the book."""
    chars = set()
    for item in book.items:
        if isinstance(item, epub.EpubHtml):
            content = item.content
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='ignore')
            chars.update(ord(c) for c in content)
    return chars

def subset_font(font_data, unicodes, flavor):
    """Subset font_data (bytes) to only the given unicode codepoints. Returns (bytes, warnings)."""
    options = ft_subset.Options()
    options.flavor = flavor  # None for ttf, 'woff2' etc for web fonts
    options.ignore_missing_unicodes = True
    font = TTFont(io.BytesIO(font_data))

    # Some fonts (e.g. Huakang) have more hmtx data than fontTools expects for
    # the declared glyph count, causing subsetting to produce an empty result.
    # Replace the malformed hmtx with a synthetic valid one so fontTools can
    # subset it normally. Glyph advance widths are set to hhea.advanceWidthMax.
    if 'hhea' in font:
        n_metrics = font['hhea'].numberOfHMetrics
        n_glyphs = len(font.getGlyphOrder())
        expected_hmtx_size = n_metrics * 4 + max(0, n_glyphs - n_metrics) * 2
        raw_hmtx_size = len(font.reader['hmtx']) if 'hmtx' in font.reader else 0
        if raw_hmtx_size > expected_hmtx_size:
            from fontTools.ttLib.tables import _h_m_t_x as hmtx_mod
            default_width = font['hhea'].advanceWidthMax
            synthetic_hmtx = hmtx_mod.table__h_m_t_x()
            synthetic_hmtx.metrics = {name: (default_width, 0) for name in font.getGlyphOrder()}
            font['hmtx'] = synthetic_hmtx

    # Some fonts (e.g. Huakang) have no Unicode cmap — only a legacy encoding
    # like Big5. fontTools subsets by Unicode, so it strips everything and
    # produces an empty result. Decode the legacy cmap into Unicode and inject
    # a proper format 4 Unicode cmap so subsetting works normally.
    if font.getBestCmap() is None:
        from fontTools.ttLib.tables._c_m_a_p import cmap_format_4
        unicode_to_glyph = {}
        for table in font['cmap'].tables:
            for cp, glyph_name in table.cmap.items():
                high = (cp >> 8) & 0xFF
                low  = cp & 0xFF
                raw  = bytes([high, low]) if high else bytes([low])
                for enc in ['big5hkscs', 'big5', 'cp950']:
                    try:
                        for ch in raw.decode(enc):
                            unicode_to_glyph.setdefault(ord(ch), glyph_name)
                        break
                    except (UnicodeDecodeError, ValueError):
                        pass
        unicode_cmap = cmap_format_4(4)
        unicode_cmap.platformID = 3
        unicode_cmap.platEncID = 1  # Unicode BMP
        unicode_cmap.language = 0
        unicode_cmap.cmap = unicode_to_glyph
        font['cmap'].tables.append(unicode_cmap)

    subsetter = ft_subset.Subsetter(options=options)
    subsetter.populate(unicodes=unicodes)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        subsetter.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue(), captured.getvalue().strip()

def wrap_cjk_spans(html_str):
    """Wrap CJK character runs in <span class="cjk"> for explicit font assignment.

    Moon+ Reader (and some other Android readers) don't fall through a CSS
    font-family fallback chain when an embedded font lacks a glyph — they jump
    straight to the system font instead.  Wrapping CJK runs in a span with a
    direct font-family rule bypasses the fallback entirely.
    """
    CJK_RE = re.compile(r'([\u3000-\u9FFF\uF900-\uFAFF\uFE10-\uFE4F\uFF00-\uFFEF]+)')
    soup = BeautifulSoup(html_str, 'html.parser')
    for text_node in list(soup.find_all(string=True)):
        parent = text_node.parent
        if parent and parent.name in ('script', 'style'):
            continue
        if parent and parent.name == 'span' and 'cjk' in parent.get('class', []):
            continue
        text = str(text_node)
        parts = CJK_RE.split(text)
        if len(parts) <= 1:
            continue
        in_heading = any(p.name in ('h1', 'h2', 'h3') for p in text_node.parents)
        css_class = 'cjk-heading' if in_heading else 'cjk'
        for part in parts:
            if not part:
                continue
            if CJK_RE.match(part):
                span = soup.new_tag('span', **{'class': css_class})
                span.string = part
                text_node.insert_before(span)
            else:
                text_node.insert_before(part)
        text_node.decompose()
    return str(soup)


def embed_fonts(book, style_content):
    """
    Embeds fonts into the EPUB book and updates CSS if necessary.
    Returns the (potentially modified) style content.
    """
    fonts_dir = "fonts"
    if not os.path.exists(fonts_dir):
        return style_content

    print("Subsetting fonts to only include characters used in the book...")
    unicodes = collect_unicode_chars(book)
    if len(unicodes) == 0:
        print("  Warning: No characters collected — fonts will be subset to empty. Check that chapters are loaded before font embedding.")
    else:
        print(f"  {len(unicodes):,} unique characters collected from book content.")

    # Map of Expected Filename in EPUB (from CSS) -> Possible local filenames
    desired_fonts = {
        "Literata.ttf": ["Literata.ttf", "Literata-Regular.ttf", "Literata[opsz,wght].ttf"],
        "FoglihtenNo07calt.otf": ["FoglihtenNo07calt.otf", "FoglihtenNo07.otf", "FoglihtenNo07.ttf", "FoglihtenNo07-Regular.otf"],
        "NotoSerifTC-Regular.ttf": ["NotoSerifTC-Regular.ttf", "NotoSerifTC.ttf"],
        "Huakang.ttf": ["Huakang.ttf", "Huakang running script.ttf", "DynaLab Huakang.ttf"],
    }
    
    for epub_name, candidate_names in desired_fonts.items():
        found_path = None
        # 1. Try exact matches from candidate list
        for local_name in candidate_names:
            p = os.path.join(fonts_dir, local_name)
            if os.path.exists(p):
                found_path = p
                break
        
        # 2. Try lenient search if not found
        if not found_path:
            for f_name in os.listdir(fonts_dir):
                normalized = f_name.lower()
                target_base = epub_name.split('.')[0].lower()
                if target_base in normalized:
                    found_path = os.path.join(fonts_dir, f_name)
                    break

        if found_path:
            with open(found_path, 'rb') as f:
                font_data = f.read()

            original_kb = len(font_data) // 1024
            print(f"  {epub_name} ({original_kb}KB)...", end=" ", flush=True)
            subsetted, ft_warnings = subset_font(font_data, unicodes, flavor=None)
            result_kb = len(subsetted) // 1024
            if result_kb == 0:
                print(f"subsetting failed — embedding full font ({original_kb}KB)")
                if ft_warnings:
                    for line in ft_warnings.splitlines():
                        line = line.strip()
                        if line:
                            print(f"    Reason: {line}")
            else:
                font_data = subsetted
                print(f"OK ({original_kb}KB -> {result_kb}KB)")
                if ft_warnings:
                    for line in ft_warnings.splitlines():
                        line = line.strip()
                        if "don't know how to subset" in line or "NOT subset" in line:
                            table = line.split()[0] if line else "unknown"
                            print(f"    Note: '{table}' is a non-standard font table and was safely removed.")
                        elif line:
                            print(f"    Warning: {line}")

            # Determine mime type
            actual_ext = os.path.splitext(found_path)[1].lower()
            if actual_ext == ".otf":
                mime = "font/otf"
            else:
                mime = "font/ttf"
            
            # Handle extension mismatch (e.g. found .ttf but CSS expects .otf)
            expected_ext = os.path.splitext(epub_name)[1].lower()
            final_filename = epub_name
            
            if actual_ext != expected_ext:
                final_filename = os.path.splitext(epub_name)[0] + actual_ext
                style_content = style_content.replace(epub_name, final_filename)
                
            font_item = epub.EpubItem(
                uid=f"font_{epub_name.replace('.', '_')}",
                file_name=f"fonts/{final_filename}",
                media_type=mime,
                content=font_data
            )
            book.add_item(font_item)
        else:
            print(f"Warning: Font {epub_name} requested by CSS but not found in {fonts_dir}.")
            
    return style_content

def create_epub(metadata_obj, chapters_data):
    print("Generating EPUB...")
    metadata = metadata_obj.get("metadata", {})
    ordered_slugs = metadata_obj.get("order", [])
    cover_image_url = metadata_obj.get("cover_image_url")
    
    book = epub.EpubBook()
    book.set_identifier("rtoc")
    book.set_title("A Regressor's Tale of Cultivation")
    book.set_language("en")
    book.add_author("엄청난 (Tremendous)")
    book.add_metadata('DC', 'description', DESCRIPTION)

    # Handle cover image
    cover_path = os.path.join(DATA_DIR, "cover.webp")
    if os.path.exists(cover_path):
        with open(cover_path, 'rb') as f:
            book.set_cover("cover.webp", f.read())
    elif cover_image_url:
        print("Warning: Cover image URL found but local image missing. Run without --force to potentially skip download if already existing (not applicable here), or check generation logs.")

    style_path = "style.css"
    with open(style_path, 'r', encoding='utf-8') as f:
        style = f.read()

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
            raw_html = f'<h1>{title}</h1><div class="date">Released: {release_date}</div>{content}'
            ch_html.content = wrap_cjk_spans(raw_html)
            ch_html.add_item(nav_css)
            book.add_item(ch_html)
            chapters.append(ch_html)

    # Embed fonts AFTER chapters are added so all characters can be collected
    style = embed_fonts(book, style)
    nav_css.content = style  # Update nav CSS with any font filename changes

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ['nav'] + chapters
    epub.write_epub(OUTPUT_EPUB, book, {})
    print(f"EPUB generated successfully: {OUTPUT_EPUB}")

async def main(limit_indices=None, force_rebuild=False):
    ensure_dirs()
    metadata_obj = load_json(METADATA_FILE)
    
    # Always check for new chapters
    metadata_obj = await generate_metadata_async(existing_metadata=metadata_obj, force_full_scan=force_rebuild)
    if metadata_obj:
        save_json(METADATA_FILE, metadata_obj)
    else:
        print("Error: Could not retrieve metadata.")
        return

    metadata = metadata_obj.get("metadata", {})
    ordered_slugs = metadata_obj.get("order", [])
    
    chapters_data = load_json(CHAPTERS_FILE)
    
    # Sync metadata titles to chapters_data if chapters_data has generic titles
    data_changed = False
    for slug, meta in metadata.items():
        # Clean exception: Never sync/overwrite Chapter 0 (Prologue)
        if slug == "chapter-0":
            continue

        if slug in chapters_data:
            ch_title = chapters_data[slug].get("title", "")
            meta_title = meta.get("title", "")
            
            # If metadata title is "richer" (has subtitle) and chapter title doesn't, update chapter title
            if ":" in meta_title and ":" not in ch_title:
                print(f"Syncing title for {slug}: {ch_title} -> {meta_title}")
                chapters_data[slug]["title"] = meta_title
                data_changed = True
            # Or if metadata title is just longer/different and current is generic
            elif meta_title != ch_title and ch_title == slug:
                chapters_data[slug]["title"] = meta_title
                data_changed = True
    
    if data_changed:
        save_json(CHAPTERS_FILE, chapters_data)
        print("Updated chapters.json with improved titles from metadata.")
    
    queue = asyncio.Queue()
    for idx, slug in enumerate(ordered_slugs):
        if limit_indices and idx not in limit_indices:
            continue
            
        meta = metadata[slug]
        
        # Check if already generated
        already_generated = False
        if slug == "chapter-807-808":
            if "chapter-807" in chapters_data and "chapter-808" in chapters_data:
                already_generated = True
        elif slug in chapters_data:
            # Check if title is just the slug (indicates retry might be needed or meta was better)
            ch_data = chapters_data[slug]
            if ch_data.get("title") == slug and slug.startswith("chapter-"):
                already_generated = False
            else:
                already_generated = True
        
        if already_generated and not force_rebuild:
            continue
            
        await queue.put((meta['url'], slug, meta.get('title')))

    if queue.empty():
        print("No new/missing chapters to generate.")
    else:
        print(f"Starting generation of {queue.qsize()} items...")
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

    print("Generation complete.")
    
    # Download cover if needed
    cover_url = metadata_obj.get("cover_image_url")
    if cover_url:
        cover_path = os.path.join(DATA_DIR, "cover.webp")
        if not os.path.exists(cover_path) or force_rebuild:
            print("Downloading cover image...")
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(cover_url, follow_redirects=True)
                    if resp.status_code == 200:
                        with open(cover_path, "wb") as f:
                            f.write(resp.content)
                        print("Cover image downloaded.")
                    else:
                        print(f"Failed to download cover image: Status {resp.status_code}")
            except Exception as e:
                print(f"Error downloading cover image: {type(e).__name__}: {e}")

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
