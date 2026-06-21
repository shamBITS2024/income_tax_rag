# Session Recap — Income Tax Act 2025 RAG Project

## What We Built Today

A pipeline to scrape, extract, and structurally parse the Income Tax Act 2025
into machine-readable JSON — suitable as the foundation for a legal RAG system.

---

## Phase 1 — Scraping (income_tax_act.py)

**What it does:**
- Phase 1 (browser): Uses SeleniumBase to walk all 56 pages of the section list,
  intercepts `window.open` calls to capture each section's URL, saves to `urls_manifest.json`
- Phase 2 (requests): Downloads each section's HTML page, extracts text, saves
  `section_N.html` and `section_N.txt` per section

**Key fixes made:**
- Selector was wrong — site uses `.doc-view-content-main` not `.doc-view-content-area`
- Skip logic `txt_file.exists()` was skipping empty files — added `.stat().st_size > 200` check
- Added HTML cache: if `.html` already exists, re-parse from disk instead of re-downloading
- Phase 1 now skipped entirely if `urls_manifest.json` already has entries
- Result: **553 sections scraped, 0 failed**

---

## Phase 2 — Table-Aware Text Extraction

**Problem:** `BeautifulSoup.get_text()` flattens everything — tables become unreadable blobs.

**Solution:** Custom `element_to_text()` walker that distinguishes:
- `class="list"` tables → **layout tables** (sub-clause indentation) → plain text `(a) text`
- `class="allborder"` / `class="tx"` tables → **real data tables** → markdown table format

**Key concept learned:** The site uses HTML `<table>` for two completely different purposes:
1. Visual indentation of legal sub-clauses (not real tables)
2. Actual tabular data (population thresholds, schedules)

CSS class is the reliable signal — not the word "TABLE" in the text.

**`_table_to_md()` function:** renders data tables as aligned markdown with header separator row.

---

## Phase 3 — Structural Parser (parse_section.py)

This is the most complex part. Goal: convert flat HTML into a nested JSON tree
matching the legal hierarchy.

### The 5-Level Label Hierarchy

Indian Income Tax Act uses a fixed 5-level label convention:

| Level | Label Type | Example | Name |
|-------|-----------|---------|------|
| 1 | Number | `( 5 )` | CLAUSE |
| 2 | Lowercase alpha | `( a )` | sub_clause |
| 3 | Lowercase Roman | `( i )` | sub_sub |
| 4 | Uppercase alpha | `( A )` | item |
| 5 | Uppercase Roman | `( I )` | sub_item |

**Disambiguation rule:** Check Roman BEFORE single-letter alpha in regex.
`( i )` is ALWAYS Roman numeral (level 3), never sub-clause `i` (level 2).
Indian drafting convention skips `i` as a sub-clause letter to avoid this conflict.

### HTML Structure Discovery

Each label+text lives in a `<table class="list">` with 3 cells:
```
<td> ( a ) </td>  <td>&nbsp;</td>  <td> text here </td>
```

**Bug found:** Multiple clauses share ONE `<table>` as multiple `<tr>` rows.
`table.find("tr")` only got the first row. Fix: iterate all rows via `<tbody>`.

### Stack Algorithm — Version 1 (broken)

Used level numbers 1-5 to determine parent/child/sibling:
```
Pop while stack_top.level >= current.level
```
Worked for 90% of sections. **Failed** at clause 22 where `(a)` after `(I)`:
- `(a)` = level 2, `(I)` = level 5
- Algorithm popped all the way up — `(a)` became child of clause 22, not of `(I)`

### The Key Insight — Level Numbers vs Label Types

**Broken assumption:** level number = depth in tree (2 is always shallower than 5)

**Reality:** The hierarchy is a **cycle**, not a scale:
```
num → la → lr → ua → ur → la → lr → ...
                            ↑ restart
```

After `(I)` (uppercase Roman), the NEXT child is `(a)` (lowercase alpha again).
This is the restart. Level 2 after level 5 means DEEPER, not shallower.

### Stack Algorithm — Version 2 (correct)

Instead of comparing numbers, compare **label type transitions**:

```python
NEXT_TYPE = {"num": "la", "la": "lr", "lr": "ua", "ua": "ur", "ur": "la"}

For each token with type lt:
  if NEXT_TYPE[stack_top_type] == lt  → child (push, go deeper)
  if stack_top_type == lt             → sibling (pop top, find parent)
  else                                → going up (pop, keep searching)
```

This correctly handles the restart: `ur → la` is in NEXT_TYPE, so `(a)` after `(I)`
is always treated as a child.

### TABLE Attachment (Problem 1 fix)

Real data tables (`class="allborder"`) appear as **sibling elements** in the HTML,
not inside the `<td>` cell of the list table row they belong to.

**Naive fix (rejected):** Check next sibling after every `<tr>` — O(n) per row.

**Better fix (user's idea):** Pre-index all tables in one pass:
```
content.find_all("table")  →  all tables in document order

For each table:
  class="list"      → emit token, update last_token pointer
  class="allborder" → attach to last_token as token["table"]
```

One pass, O(n) total. The `last_token` pointer always points to the token
whose text referenced "the following Table:—".

---

## Project Architecture at End of Session

```
income_tax_act.py      — scraper (phases 1+2) + text extraction
parse_section.py       — structural HTML parser → nested JSON
income_tax_sections/
  urls_manifest.json   — 553 section URLs
  section_N/
    section_N.html     — raw downloaded HTML
    section_N.txt      — human-readable extracted text (tables as markdown)
    section_N.json     — structured JSON (section_2 only so far)
PROJECT_SPEC.md        — architecture notes
```

---

## Level Reached

| Component | Status |
|-----------|--------|
| URL collection (all 553 sections) | DONE |
| HTML download (all 553 sections) | DONE |
| Text extraction with table detection | DONE |
| Structural JSON parser | DONE (section_2 proven) |
| Extend parser to all 553 sections | NEXT |
| Structured JSON → chunks for RAG | NEXT |
| Embedding + BM25 index | NOT STARTED |
| Hybrid retrieval | NOT STARTED |
| Reranker | NOT STARTED |
| LLM answer generation with citations | NOT STARTED |
| Test query set (20 queries) | USER TO WRITE |

---

## Key CS Concepts Learned/Used

1. **Stack for tree building** — any left-to-right nested structure uses a stack
   to track the "current open path" from root to current node

2. **Assumption debugging** — when an algorithm fails, ask "what assumption did I make
   that isn't true?" The broken assumption here was level=depth.

3. **Label type cycle vs linear scale** — the hierarchy is a cycle (restarts),
   not a monotone scale. Transitions between types carry the structural info, not numbers.

4. **CSS class as semantic signal** — `class="list"` vs `class="allborder"` are
   reliable semantic markers even on a site not designed for scraping

5. **One-pass pre-indexing** — instead of checking siblings per row, collect all
   elements in document order once, process sequentially with a pointer

6. **Jeremy Howard's solve-it method** — end-to-end first (even crude), measure,
   then improve one piece at a time. Never add complexity before verifying the baseline.

---

## Next Session Starting Point

Run `python parse_section.py` — it works on `section_2.html` and produces `section_2.json`.

Next step: extend `parse_section.py` to loop over all 553 sections and produce
one JSON file per section. Then build the chunk + embed pipeline.
