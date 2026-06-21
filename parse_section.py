"""
Step 1 — flat extraction + label classification from section HTML.
Goal: verify every ( X ) label is extracted cleanly and classified correctly.
Run: python parse_section.py
"""
import re
from pathlib import Path
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Label classifier
# ---------------------------------------------------------------------------
# Order matters: Roman checked BEFORE single-letter alpha (I matches both)

ROMAN_L = re.compile(r'^(i{1,3}|iv|vi{0,3}|ix|x{1,3}|xi{0,3}|xiv|xv|xvi{0,3}|xl|l)$')
ROMAN_U = re.compile(r'^(I{1,3}|IV|VI{0,3}|IX|X{1,3}|XI{0,3}|XIV|XV|XL|L)$')
NUMBER  = re.compile(r'^\d+$')

LEVEL_NAMES = {1: "CLAUSE", 2: "sub_clause", 3: "sub_sub", 4: "item", 5: "sub_item"}

def label_level(raw: str) -> tuple[int, str]:
    """Return (level, label) for a raw label string like '( a )' or '5'."""
    lbl = raw.strip().strip("()").strip()
    if NUMBER.match(lbl):        return 1, lbl
    if ROMAN_U.match(lbl):       return 5, lbl
    if re.match(r'^[A-Z]$', lbl): return 4, lbl
    if ROMAN_L.match(lbl):       return 3, lbl
    if re.match(r'^[a-z]$', lbl): return 2, lbl
    return 0, lbl  # unknown / footnote junk


# ---------------------------------------------------------------------------
# Extract tokens from HTML
# ---------------------------------------------------------------------------

DATA_TABLE_CLASSES = {"allborder", "tx"}


def _table_rows(table: Tag):
    """Yield <tr> elements from a table, handling tbody/thead/tfoot."""
    for child in table.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "tr":
            yield child
        elif child.name in ("tbody", "thead", "tfoot"):
            for tr in child.children:
                if isinstance(tr, Tag) and tr.name == "tr":
                    yield tr


def _render_data_table(table: Tag) -> str:
    """Render an allborder/tx table as a markdown table string."""
    from income_tax_act import _table_to_md
    return _table_to_md(table)


def extract_tokens(html_path: Path) -> list[dict]:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="replace"), "html.parser")

    content = (
        soup.select_one(".doc-view-content-main")
        or soup.select_one(".etds-main-content")
        or soup.select_one("article")
    )
    if not content:
        return []

    tokens = []
    last_token = None  # track most recently added token

    # Single pass: process ALL tables in document order
    for table in content.find_all("table"):
        cls = set(table.get("class") or [])

        if cls & DATA_TABLE_CLASSES:
            # Attach this data table to whatever token came just before it
            if last_token is not None:
                last_token["table"] = _render_data_table(table)

        elif "list" in cls:
            for tr in _table_rows(table):
                cells = tr.find_all(["td", "th"], recursive=False)
                if len(cells) < 3:
                    continue

                raw_label = cells[0].get_text(strip=True)
                text      = re.sub(r"\s+", " ", cells[2].get_text(separator=" ", strip=True))

                if "(" not in raw_label and ")" not in raw_label:
                    continue

                level, label = label_level(raw_label)
                token = {"level": level, "label": label, "text": text, "raw": raw_label}
                tokens.append(token)
                last_token = token

    return tokens


# ---------------------------------------------------------------------------
# Main — print flat list, flag unknowns
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Step 2 — build tree using label-type-aware stack
# ---------------------------------------------------------------------------

# Label type for each level number
TYPE_OF = {1: "num", 2: "la", 3: "lr", 4: "ua", 5: "ur"}

# What label type is expected as the NEXT child in the hierarchy.
# ur→la is the "restart": after Roman-upper, the cycle begins again with lower-alpha.
NEXT_TYPE = {"num": "la", "la": "lr", "lr": "ua", "ua": "ur", "ur": "la"}


def build_tree(tokens: list[dict]) -> list[dict]:
    """
    Convert flat token list into a nested tree using label-type transitions.

    Stack entries: (label_type, node)

    For each token with label_type lt:
      • NEXT_TYPE[top_type] == lt  → lt is a child of top (push, go deeper)
      • top_type == lt             → lt is a sibling (pop top, find parent above)
      • otherwise                 → going up (pop, keep searching)
    """
    def make_node(t):
        node = {"label": t["label"], "text": t["text"], "children": []}
        if "table" in t:
            node["table"] = t["table"]
        return node

    roots = []
    stack = []  # each entry: (label_type: str, node: dict)

    for t in tokens:
        if t["level"] == 0:
            continue

        node = make_node(t)
        lt = TYPE_OF.get(t["level"], "unknown")

        # Find the correct parent using label-type transitions
        while stack:
            top_type, top_node = stack[-1]

            if NEXT_TYPE.get(top_type) == lt:
                # top is the parent (lt is expected child of top)
                break
            elif top_type == lt:
                # same type → sibling, pop this entry and re-check parent above
                stack.pop()
            else:
                # neither parent nor sibling → going up
                stack.pop()

        if stack:
            _, parent_node = stack[-1]
            parent_node["children"].append(node)
        else:
            roots.append(node)  # top-level: only num (CLAUSE) should reach here

        stack.append((lt, node))

    return roots


def print_tree(nodes: list[dict], indent: int = 0):
    """Pretty-print the tree so we can visually verify nesting."""
    prefix = "  " * indent
    for node in nodes:
        text_preview = node["text"][:70] + ("…" if len(node["text"]) > 70 else "")
        table_flag = " [TABLE]" if "table" in node else ""
        children_count = f"  [{len(node['children'])} children]" if node["children"] else ""
        print(f"{prefix}({node['label']}) {text_preview}{table_flag}{children_count}")
        print_tree(node["children"], indent + 1)


# ---------------------------------------------------------------------------
# Step 3 — wrap tree in section metadata and write JSON
# ---------------------------------------------------------------------------

def section_title(soup) -> str:
    """Extract the section title from the page — first <h2> or <h3> in content."""
    for tag in ("h2", "h3", "h1"):
        el = soup.select_one(f".doc-view-content-main {tag}, .etds-main-content {tag}")
        if el:
            return el.get_text(strip=True)
    # fallback: first non-empty line of content text
    content = soup.select_one(".doc-view-content-main") or soup.select_one(".etds-main-content")
    if content:
        for line in content.get_text(separator="\n").splitlines():
            line = line.strip()
            if line:
                return line
    return ""


def parse_section(html_path: Path) -> dict:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="replace"), "html.parser")

    # Section number from folder name e.g. section_2 → "2"
    sec_num = html_path.parent.name.replace("section_", "")

    tokens = extract_tokens(html_path)
    tree   = build_tree(tokens)

    return {
        "act": "Income Tax Act 2025",
        "section": sec_num,
        "section_title": section_title(soup),
        "total_clauses": len(tree),
        "clauses": tree,
    }


if __name__ == "__main__":
    import json

    html_path = Path(r"D:\Interview\rag\income_tax_sections\section_2\section_2.html")
    out_path  = html_path.parent / "section_2.json"

    data = parse_section(html_path)

    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Written: {out_path}")
    print(f"Section : {data['section']} — {data['section_title']}")
    print(f"Clauses : {data['total_clauses']}")
    print()

    # Spot-check clause 22 in JSON
    clause_22 = next((c for c in data["clauses"] if c["label"] == "22"), None)
    print("=== Clause 22 (JSON snippet) ===")
    print(json.dumps(clause_22, ensure_ascii=False, indent=2)[:2000])
