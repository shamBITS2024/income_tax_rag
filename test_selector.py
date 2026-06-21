from bs4 import BeautifulSoup

with open(r"D:\Interview\rag\income_tax_sections\section_15\section_15.html", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, "html.parser")

for sel in [".doc-view-content-area", ".doc-view-content-main", ".etds-main-content", ".journal-content-article", "article", "main"]:
    el = soup.select_one(sel)
    if el:
        text = el.get_text(separator="\n", strip=True)
        print(f"{sel}: FOUND, len={len(text)}, preview={text[:150]!r}")
    else:
        print(f"{sel}: not found")
