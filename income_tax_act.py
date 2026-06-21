"""
Income Tax Act 2025 Section Scraper — SeleniumBase edition

Phase 1 — URL collection (browser)
  Walk all 56 pages of the section list.
  For each section: open modal → intercept window.open → capture the section URL.
  Saves urls_manifest.json so it can resume if interrupted.

Phase 2 — Content download (requests)
  For each URL, fetch the section page and save:
    income_tax_sections/section_N/section_N.html
    income_tax_sections/section_N/section_N.txt

Logs everything to income_tax_sections/scraping_log.txt
"""

import re
import json
import time
import logging
from pathlib import Path
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup, Tag, NavigableString
from seleniumbase import SB
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException


BASE_URL = "https://www.incometaxindia.gov.in/income-tax-act-2025"
HOST = "https://www.incometaxindia.gov.in"
OUTPUT_DIR = Path("income_tax_sections")
MANIFEST_FILE = OUTPUT_DIR / "urls_manifest.json"
SECTIONS_PER_PAGE = 10
PAGE_DELAY = 2.5
CLICK_DELAY = 1.2
DOWNLOAD_DELAY = 0.8

# Intercept window.open calls and store the URL in window.__capturedUrl
INTERCEPT_JS = """
window.__capturedUrl = null;
window.open = function(url) {
    window.__capturedUrl = url;
    return null;
};
"""


# ---------------------------------------------------------------------------
# Table-aware text extraction
# ---------------------------------------------------------------------------

MAX_CELL_WIDTH = 80  # truncate cell text beyond this to keep tables readable


def _direct_rows(table: Tag):
    """Yield <tr> elements belonging to this table only (not nested tables)."""
    for child in table.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "tr":
            yield child
        elif child.name in ("thead", "tbody", "tfoot"):
            for tr in child.children:
                if isinstance(tr, Tag) and tr.name == "tr":
                    yield tr


def _cell_text(cell: Tag) -> str:
    """Flatten a <td>/<th> to a single line, rendering nested tables inline."""
    parts = []
    for child in cell.children:
        if isinstance(child, NavigableString):
            t = str(child).strip()
            if t:
                parts.append(t)
        elif isinstance(child, Tag):
            if child.name == "table":
                # nested table: collapse to one line so outer cell stays single-line
                parts.append("[" + child.get_text(separator=" ", strip=True) + "]")
            else:
                t = child.get_text(separator=" ", strip=True)
                if t:
                    parts.append(t)
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(text) > MAX_CELL_WIDTH:
        text = text[:MAX_CELL_WIDTH] + "…"
    return text


# Tables with these classes are real data tables → render as markdown.
# class="list" and anything else is a layout table (sub-clause indentation) → plain text.
_DATA_TABLE_CLASSES = {"allborder", "tx"}


def _is_data_table(table: Tag) -> bool:
    cls = set((table.get("class") or []))
    return bool(cls & _DATA_TABLE_CLASSES)


def _table_to_md(table: Tag) -> str:
    """Convert a real data <table> to a markdown-style plain-text table."""
    rows = [
        [_cell_text(c) for c in tr.find_all(["td", "th"], recursive=False)]
        for tr in _direct_rows(table)
    ]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""

    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]

    widths = [max(max(len(r[j]) for r in rows), 3) for j in range(ncols)]

    def fmt_row(r):
        return "| " + " | ".join(r[j].ljust(widths[j]) for j in range(ncols)) + " |"

    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"

    lines = [fmt_row(rows[0]), sep] + [fmt_row(r) for r in rows[1:]]
    return "\n".join(lines)


def _layout_table_to_text(table: Tag) -> str:
    """Convert a layout <table> (sub-clause indentation) to plain indented text."""
    lines = []
    for tr in _direct_rows(table):
        cells = [_cell_text(c) for c in tr.find_all(["td", "th"], recursive=False)]
        # Strip empty spacer cells, join what remains
        parts = [c for c in cells if c.strip()]
        if parts:
            lines.append(" ".join(parts))
    return "\n".join(lines)


_SKIP_TAGS = {"style", "script", "noscript", "head"}


def element_to_text(el) -> str:
    """Recursively convert HTML element to text.

    Real data tables (class allborder / tx) → markdown table.
    Layout tables (class list, sub-clause indentation) → plain indented text.
    """
    if isinstance(el, NavigableString):
        return str(el).strip()
    if not isinstance(el, Tag):
        return ""
    if el.name in _SKIP_TAGS:
        return ""
    if el.name == "table":
        if _is_data_table(el):
            return _table_to_md(el)
        return _layout_table_to_text(el)

    parts = [t for child in el.children if (t := element_to_text(child))]

    block_tags = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                  "li", "section", "article", "blockquote"}
    sep = "\n\n" if el.name in block_tags else "\n"
    return sep.join(parts)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    OUTPUT_DIR.mkdir(exist_ok=True)
    log_file = OUTPUT_DIR / "scraping_log.txt"

    logger = logging.getLogger("ita_scraper")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        logger.handlers.clear()

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info("=" * 60)
    logger.info("Income Tax Act 2025 Scraper")
    logger.info(f"URL    : {BASE_URL}")
    logger.info(f"Output : {OUTPUT_DIR.absolute()}")
    logger.info("=" * 60)
    return logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def section_folder(sec_num: str) -> Path:
    folder = OUTPUT_DIR / f"section_{sec_num}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def extract_section_num(text: str) -> Optional[str]:
    m = re.search(r"[Ss]ection\s+(\d+[A-Za-z]*)", text)
    return m.group(1) if m else None


def sort_key(sec_num: str) -> tuple:
    m = re.match(r"(\d+)([A-Za-z]*)", sec_num)
    if m:
        return (int(m.group(1)), m.group(2))
    return (9999, sec_num)


def load_manifest() -> Dict[str, str]:
    if MANIFEST_FILE.exists():
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    return {}


def save_manifest(manifest: Dict[str, str], logger: logging.Logger):
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.debug(f"Manifest saved: {len(manifest)} entries")


# ---------------------------------------------------------------------------
# Phase 1 — collect section URLs via window.open interception
# ---------------------------------------------------------------------------

def collect_urls(driver, logger: logging.Logger) -> Dict[str, str]:
    manifest = load_manifest()
    if manifest:
        logger.info(f"[URL] Resuming — manifest has {len(manifest)} entries already")

    wait = WebDriverWait(driver, 25)

    logger.info(f"[URL] Opening {BASE_URL}")
    driver.get(BASE_URL)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".sections-list")))
    time.sleep(3)

    # Inject intercept on fresh page
    driver.execute_script(INTERCEPT_JS)

    # Detect total pages
    total_pages = 56
    try:
        pag = driver.find_element(By.CSS_SELECTOR, ".pagination-first-section")
        m = re.search(r"of\s+(\d+)\s+items", pag.text, re.IGNORECASE)
        if m:
            total = int(m.group(1))
            total_pages = (total + SECTIONS_PER_PAGE - 1) // SECTIONS_PER_PAGE
            logger.info(f"[URL] Total sections: {total}, pages: {total_pages}")
    except Exception as e:
        logger.warning(f"[URL] Pagination unreadable: {e} — using default {total_pages}")

    page = 1
    global_idx = 0

    while page <= total_pages:
        logger.info(f"[URL] === Page {page}/{total_pages} ===")
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".sections-list")))
        time.sleep(1)

        items = driver.find_elements(By.CSS_SELECTOR, "li.sections-item")
        logger.info(f"[URL]   {len(items)} items found")

        for item_el in items:
            global_idx += 1

            try:
                sec_name = item_el.find_element(By.CSS_SELECTOR, "span.section-name").text.strip()
            except NoSuchElementException:
                sec_name = f"section_{global_idx}"

            sec_num = extract_section_num(sec_name) or str(global_idx)

            if sec_num in manifest:
                logger.info(f"  [{global_idx}] SKIP {sec_name} (already in manifest)")
                continue

            logger.info(f"  [{global_idx}] {sec_name}")

            # Click section-link to open modal
            try:
                sec_link = item_el.find_element(By.CSS_SELECTOR, "button.section-link")
                driver.execute_script("arguments[0].click();", sec_link)
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".document-viewer-modal")))
                time.sleep(1.2)
            except Exception as e:
                logger.warning(f"    Cannot open modal: {e}")
                _close_modal(driver)
                continue

            # Re-inject intercept (React may have re-rendered)
            driver.execute_script(INTERCEPT_JS)

            # Click "Open In New Tab"
            url = None
            try:
                btn = driver.find_element(By.CSS_SELECTOR, ".document-viewer-modal .btn-open-in-new-tab")
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1.5)
                raw = driver.execute_script("return window.__capturedUrl;")
                if raw:
                    url = raw if raw.startswith("http") else HOST + raw
                    logger.info(f"    -> {url}")
                else:
                    logger.warning(f"    window.open not triggered for {sec_name}")
            except NoSuchElementException:
                logger.warning(f"    Open-In-New-Tab button not found for {sec_name}")
            except Exception as e:
                logger.error(f"    Error getting URL for {sec_name}: {e}")

            if url:
                manifest[sec_num] = url
                save_manifest(manifest, logger)

            _close_modal(driver)
            time.sleep(CLICK_DELAY)

        if page >= total_pages:
            break

        # Navigate to next page
        try:
            next_btn = driver.find_element(By.ID, "pagination-next-button")
            if next_btn.get_attribute("disabled"):
                logger.info("[URL] Next button disabled — end of pages")
                break
            driver.execute_script("arguments[0].click();", next_btn)
            driver.execute_script(INTERCEPT_JS)  # re-inject after page change
            time.sleep(PAGE_DELAY)
            page += 1
        except Exception as e:
            logger.error(f"[URL] Cannot go to next page: {e}")
            break

    logger.info(f"[URL] Collection complete: {len(manifest)} URLs")
    return manifest


def _close_modal(driver):
    try:
        btn = driver.find_element(By.CSS_SELECTOR, ".document-viewer-modal .close-doc-view-modal")
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.6)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase 2 — download section content pages
# ---------------------------------------------------------------------------

def download_sections(manifest: Dict[str, str], logger: logging.Logger):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })

    sorted_items = sorted(manifest.items(), key=lambda x: sort_key(x[0]))
    total = len(sorted_items)
    success = skipped = failed = 0

    logger.info(f"[DL] Downloading {total} sections")

    for i, (sec_num, url) in enumerate(sorted_items, 1):
        folder = section_folder(sec_num)
        txt_file = folder / f"section_{sec_num}.txt"
        html_file = folder / f"section_{sec_num}.html"

        if txt_file.exists() and txt_file.stat().st_size > 200:
            logger.info(f"  [{i}/{total}] SKIP section_{sec_num} (already saved)")
            skipped += 1
            continue

        if html_file.exists():
            logger.info(f"  [{i}/{total}] Re-parsing section_{sec_num} from cached HTML")
            soup = BeautifulSoup(html_file.read_text(encoding="utf-8", errors="replace"), "html.parser")
        else:
            logger.info(f"  [{i}/{total}] Downloading section_{sec_num}: {url}")
            try:
                resp = session.get(url, timeout=30)
                resp.raise_for_status()
            except requests.HTTPError as e:
                logger.error(f"    HTTP error: {e}")
                failed += 1
                time.sleep(DOWNLOAD_DELAY)
                continue
            except Exception as e:
                logger.error(f"    Request error: {e}")
                failed += 1
                time.sleep(DOWNLOAD_DELAY)
                continue
            html_file.write_bytes(resp.content)
            time.sleep(DOWNLOAD_DELAY)
            soup = BeautifulSoup(resp.text, "html.parser")

        # Extract main content — try selectors in order of preference
        content = (
            soup.select_one(".doc-view-content-main")
            or soup.select_one(".etds-main-content")
            or soup.select_one(".doc-view-content-area")
            or soup.select_one("article")
            or soup.select_one("main")
            or soup.select_one("body")
        )

        title_el = soup.select_one("h1") or soup.select_one("title")
        title = title_el.get_text(strip=True) if title_el else f"Section {sec_num}"

        text = element_to_text(content) if content else ""
        txt_file.write_text(f"{title}\n{'=' * 60}\n\n{text}", encoding="utf-8")

        logger.info(f"    Saved: {folder.name}/  ({len(text):,} chars)")
        success += 1

    logger.info("=" * 60)
    logger.info(f"[DL] Done: success={success}  skipped={skipped}  failed={failed}")
    logger.info("=" * 60)
    return success, skipped, failed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger = setup_logging()

    # Phase 1 — skip if manifest already has entries (resume-friendly)
    manifest = load_manifest()
    if manifest:
        logger.info(f"[URL] Manifest already has {len(manifest)} entries — skipping browser phase")
    else:
        with SB(uc=True, headless=True) as sb:
            manifest = collect_urls(sb.driver, logger)

    if not manifest:
        logger.error("No URLs collected — cannot proceed to download")
        return

    logger.info(f"URL manifest ready: {len(manifest)} sections")

    # Phase 2 — requests: download and save content
    success, skipped, failed = download_sections(manifest, logger)

    logger.info(f"DONE  success={success}  skipped={skipped}  failed={failed}")
    logger.info(f"Output: {OUTPUT_DIR.absolute()}")


if __name__ == "__main__":
    main()
