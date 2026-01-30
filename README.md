# A Regressor's Tale of Cultivation Scraper

A high-performance Python script to scrape "A Regressor's Tale of Cultivation" from wetriedtls.com and package it into a clean EPUB file to import into a reader app.

## Features

- **Blazing Fast**: Uses `asyncio` and `playwright` to scrape chapters in parallel.
- **Robust & Reliable**: Includes automatic retries and exponential backoff to handle timeout errors and network blips.
- **Incremental Updates**: Detects already scraped chapters and only fetches new ones. Re-runs will only download what's missing.
- **Smart Formatting**:
  - Automatically splits the combined Chapter 807-808 page.
  - Removes "Spoiler" tags, dates, and promotional content (Discord/Patreon/Ko-fi).
  - Includes release dates in the chapter headers.
  - Sets the correct author: **엄청난 (Tremendous)**.

## Prerequisites

- Python 3.8+
- [Playwright](https://playwright.dev/python/docs/intro)

## Installation

1. Clone or download this repository.
2. Install the required Python packages:
   ```bash
   pip install playwright ebooklib beautifulsoup4 lxml
   ```
3. Install the Playwright browser binaries:
   ```bash
   playwright install chromium
   ```

## Usage

### 1. Full Scrape
To download all available free chapters (0 to ~809+) and generate the EPUB:
```bash
python3 scraper.py
```
The script will first fetch the latest chapter metadata and then begin the parallel download process.

### 2. Updating Your Ebook
When a new chapter is released, just run the script again. It will skip existing data, download the new chapter(s), and rebuild the `A_Regressors_Tale_of_Cultivation.epub` file.

### 3. Scrape Specific Chapters
If you want to re-scrape or test specific chapter numbers:
```bash
python3 scraper.py 0 1 100 807808
```

## Storage
- **`data/metadata.json`**: Stores chapter URLs, clean titles, and release dates.
- **`data/chapters.json`**: Stores the processed HTML content of each chapter.
- **`A_Regressors_Tale_of_Cultivation.epub`**: The final output file.

## Troubleshooting
If you encounter a `TimeoutError`, don't worry—the script is configured to retry automatically. If it persists, check your internet connection or the site's status.
