"""Test: section_15 (layout table) and section_539 (real TABLE)."""
import sys
sys.path.insert(0, r"D:\Interview\rag")
from pathlib import Path
from bs4 import BeautifulSoup
from income_tax_act import element_to_text

def extract(sec_num):
    html_file = Path(fr"D:\Interview\rag\income_tax_sections\section_{sec_num}\section_{sec_num}.html")
    soup = BeautifulSoup(html_file.read_text(encoding="utf-8", errors="replace"), "html.parser")
    content = (
        soup.select_one(".doc-view-content-main")
        or soup.select_one(".etds-main-content")
        or soup.select_one("article")
    )
    return element_to_text(content) if content else ""

print("=== SECTION 15 (sub-clauses, should be plain text) ===")
print(extract("15")[:600])
print()
print("=== SECTION 539 (TABLE keyword, should be markdown table) ===")
print(extract("539")[:1200])
