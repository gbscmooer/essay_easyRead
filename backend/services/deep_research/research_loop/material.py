"""Bounded body sampling across overview, technical content and discussion."""
import re

from ..document_evidence import DocumentEvidenceReader, normalize_text


def body_material(asset_id, text, queries, *, max_chars=12000, focus_queries=(), return_passages=False, primary_targets=(), exclude_quotes=(), targeted=False):
    reader = DocumentEvidenceReader(asset_id, text)
    # PDF extraction often creates one paragraph per page. Split those pages into
    # smaller contiguous windows so an early page cannot consume the whole allowance.
    spans = []
    for block in reader.blocks:
        for match in re.finditer(r"\S[\s\S]{0,1399}(?=\s|\Z)", block.text):
            position = block.char_start + match.start()
            spans.append((position, match.group(0)))
    if not spans:
        return [] if return_passages else ""
    references = re.search(r"(?mi)^\s*(?:\d+[.\s]+)?(?:references|bibliography)\s*$", text)
    end = references.start() if references else len(text)
    body = [(pos, passage) for pos, passage in spans if pos < end]
    if not body:
        return [] if return_passages else ""
    groups = list(focus_queries) or ["taxonomy classification categories evolution",
              "method reconstruction feature detection segmentation learning",
              "challenge limitation future direction conclusion"]
    technical_range = None
    if focus_queries:
        headings = list(re.finditer(r"(?m)^[ \t]*(\d+)(?:\.[ \t]+|[ \t]*\n[ \t]*)([A-Za-z][^\n]{2,100})$", text[:end]))
        for i, heading in enumerate(headings):
            title = heading.group(2).casefold()
            if (re.search(r"\b(?:method|approach|framework)\b", title) or
                    any(name.casefold() in title for name in primary_targets)):
                technical_range = (heading.start(), headings[i + 1].start() if i + 1 < len(headings) else end)
                break
    selected = []
    used = 0

    def add(item):
        nonlocal used
        pos, passage = item
        # Source offsets are derived from the actual immutable text, not page-level
        # quote locations (which may encompass a whole page).
        for quote in exclude_quotes:
            start = text.find(quote)
            if start >= 0 and pos < start + len(quote) and start < pos + len(passage):
                return False
        if focus_queries and any(pos < old_pos + len(old_passage) and old_pos < pos + len(passage)
                                 for old_pos, old_passage in selected):
            return False
        if any(abs(pos - old_pos) < min(1100, max(40, end // 12)) for old_pos, _ in selected):
            return False
        if used + len(passage) + 2 > max_chars:
            return False
        selected.append(item)
        used += len(passage) + 2
        return True

    # Keep one introduction window, then reserve equal space for different body roles.
    if targeted:
        pass
    elif focus_queries and max_chars >= 5000:
        abstract = re.search(r"(?mi)^\s*abstract\s*$", text[:min(end, 8000)])
        introduction = re.search(r"(?mi)^\s*1(?:\.[ \t]+|[ \t]*\n[ \t]*|[ \t]+)introduction\s*$", text[:min(end, 10000)])
        if abstract and introduction and abstract.end() < introduction.start() and introduction.start() - abstract.start() <= 3000:
            # Use the complete abstract instead of truncating it after author lists.
            add((abstract.start(), text[abstract.start():introduction.start()].rstrip()))
        else:
            first = body[0][0]
            add((first, text[first:min(first + 2200, end)].rstrip()))
    else:
        add(body[0])
    if technical_range and not targeted:
        start, stop = technical_range
        add((start, text[start:min(start + 1400, stop)].rstrip()))
    elif focus_queries and not targeted:
        groups = ["method architecture mechanism features", *groups]
    if focus_queries and not targeted:
        # Implementation prose often states exactly which weights/data are used;
        # keyword ranking alone otherwise prefers large benchmark tables.
        implementation = re.search(r"(?mi)^[ \t]*(?:\d[\d.]*[ \t]+)?implementation details\b", text[:end])
        if implementation:
            start = implementation.start()
            add((start, text[start:min(start + 1700, end)].rstrip()))
        groups = ["training frozen parameters optimization loss", "inference reference memory patch window features", *groups]
    for query in groups:
        ranked = sorted(body, key=lambda item: (
            not (technical_range and technical_range[0] <= item[0] < technical_range[1]) if focus_queries and not targeted else False,
            -(reader._score(item[1], query) + (2 if item[0] > end * .15 else 0)
              + (6 if not targeted and technical_range and technical_range[0] <= item[0] < technical_range[1] else 0)
              + (3 if any(re.search(r"(?m)^\s*(?:\d+[.\s]+)?" + re.escape(term) + r"\b", item[1], re.I)
                          for term in query.split()) else 0)), item[0]))
        count = 0
        for item in ranked:
            if reader._score(item[1], query) <= 0:
                continue
            if add(item):
                count += 1
            if count == (2 if targeted else 1 if focus_queries else 2):
                break
    if targeted:
        return ([{"passage_id": f"p_{pos}", "text": passage} for pos, passage in sorted(selected)] if return_passages
                else "\n\n".join(passage for _, passage in sorted(selected)))
    # Remaining room goes to the actual topic, rather than filling from the first pages.
    ranked = sorted(body, key=lambda item: (
        not (technical_range and technical_range[0] <= item[0] < technical_range[1]) if focus_queries else False,
        -sum(reader._score(item[1], q) for q in queries)))
    for item in ranked:
        add(item)
    if return_passages:
        return [{"passage_id": f"p_{pos}", "text": passage} for pos, passage in sorted(selected)]
    return "\n\n".join(passage for _, passage in sorted(selected))


def paper_description(fact):
    """Reject clear descriptions of a paper's contents; not a semantic truth test."""
    text = normalize_text(fact)
    return bool(re.match(r"^(?:该综述|这篇综述|本综述|本文|该文|这篇论文|该论文|该调查|该研究|综述)", text)
                or re.search(r"(?:被重点讨论|讨论.{0,60}之后.{0,30}(?:展开|讨论)|对.{0,60}文献.{0,20}分类|"
                             r"各种技术.{0,60}(?:被分析|被讨论)|提供对未来研究方向的见解)", text))
