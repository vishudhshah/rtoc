# A Regressor's Tale of Cultivation EPUB Generator

A Python script to generate an EPUB of ***A Regressor's Tale of Cultivation*** from [We Tried TLS](https://wetriedtls.com/series/a-regressors-tale-of-cultivation).

## Features
- **Built for E-Readers**: Generates a clean EPUB with a book cover, Table of Contents, and metadata.
- **Ad-Free Experience**: Automatically removes website clutter, pop-ups, and promotional text.
- **Automatic Updates**: Run it once to get the whole book; run it again later to pick up only the newly released chapters.
- **Smart Formatting**: Correctly handles tricky merged chapters (like 807-808), includes author's notes/Q&As, and adds official cover art.
- **High Performance**: Optimized for speed, limited only by your internet connection.

## Quick Start
1. **Install dependencies:**
   ```bash
   # You may need to use 'pip' or 'pip3' depending on your setup
   pip3 install playwright ebooklib beautifulsoup4 lxml
   playwright install chromium
   ```
2. **Run the generator:**
   ```bash
   # You may need to use 'python' or 'python3' depending on your setup
   python3 main.py
   ```
   *Note: Requires an active internet connection. The final EPUB and a cache folder (`data/`) will be generated in the current directory.*

## Advanced Usage
- **Specific Chapters** (by index):
  ```bash
  python3 main.py 0 1 100 807808
  ```
- **Force Re-generate** (bypass cache):
  ```bash
  python3 main.py --force
  ```
- **Force specific chapters**:
  ```bash
  python3 main.py 0 1 --force
  ```

## Which E-Reader to use?
The generated EPUB is standard and should work on any modern reader:
- **Android**: I use [Moon+ Reader](http://www.moondownload.com/) (Free). Other great options include [ReadEra](https://readera.org/), [Librera](https://librera.mobi/), and [Lithium](https://play.google.com/store/apps/details?id=com.faultexception.reader). [Google Play Books](https://play.google.com/store/apps/details?id=com.google.android.apps.books) also works but has less features.
- **iOS/Mac**: The built-in **Apple Books** app handles these files perfectly.
- **Kindle**: You can easily import the EPUB file using the [Send to Kindle](https://www.amazon.com/sendtokindle) service.

## Contributing
Issues and feature requests are welcome!
- **Found a bug?** Please report it through [GitHub Issues](https://github.com/vishudhshah/rtoc/issues).
- **Want to contribute?** Submit a Pull Request (PR) with a clear description of your changes. I'll review them as soon as I can.
- **Enjoying the project?** Consider giving it a star!

## Disclaimer
- This tool is intended for personal, offline reading for fans who want a better mobile experience. Please do not use this tool for mass distribution or commercial purposes.
- **Free Chapters Only**: To avoid legal consequences and respect the translators, this script **only generates freely available chapters**. 
- **Adaptability**: While written specifically for *A Regressor's Tale of Cultivation*, the logic can theoretically be adapted for any series on We Tried TLS with some minor adjustments to handle series-specific edge cases.

To respect the hard work of the original author and the translation team at We Tried TLS, **this repository does not host the generated EPUB file.** Consider supporting the original creators!
- **Official Site:** [We Tried TLS](https://wetriedtls.com/series/a-regressors-tale-of-cultivation)
- **Discord:** Join the [We Tried Discord](https://dsc.gg/wetried) for community updates and ko-fi link.
- **Official Author:** Support the author of the webnovel, 엄청난 (Tremendous), on [Munpia](https://novel.munpia.com/346981) or [Naver](https://series.naver.com/novel/detail.series?productNo=9807283).
