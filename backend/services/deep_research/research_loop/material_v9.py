"""Role-aware, bounded source windows for overview and mechanism reading."""
import re

from ..document_evidence import DocumentEvidenceReader
from .material import body_material, paper_description


def outside_scope(question, fact):
    image_only = re.search(r'只(?:研究|讨论|考虑)[^。；]{0,12}图像|only[^.;]{0,24}images?', question, re.I)
    return bool(image_only and re.search(r'点云|三维|\b3D\b|视频|时间序列', fact, re.I) and
        not re.search(r'点云|三维|\b3D\b|视频|时间序列', question, re.I))


def description_only(fact):
    # A concrete, attributed taxonomy is content; a count or a chapter agenda is not.
    if re.search(r'分类|类别|路线|分为|包括|归纳', fact) and re.search(r'重建|特征|记忆|分布|蒸馏|距离|判据|机制|模型|方法', fact) and re.search(r'[:：、]|(?:和|与|以及|及)', fact):
        return False
    return paper_description(fact)


def overview_material(asset_id, text, queries, *, max_chars=11000, focus_queries=(), exclude_quotes=(), targeted=False, question=''):
    reader = DocumentEvidenceReader(asset_id, text)
    reference = re.search(r'(?mi)^\s*(?:\d+[.\s]+)?(?:references|bibliography)\s*$', text)
    end = reference.start() if reference else len(text)
    headings = list(re.finditer(r'(?m)^[ \t]*(\d+)\.[ \t]+([A-Za-z0-9][^\n]{2,100})$', text[:end]))
    blocked = []
    if outside_scope(question, '3D'):
        for index, heading in enumerate(headings):
            if re.search(r'\b3D\b|point clouds?|multimodal|video|time.series', heading.group(2), re.I):
                blocked.append((heading.start(), headings[index+1].start() if index+1 < len(headings) else end))
    windows = []
    for block in reader.blocks:
        for match in re.finditer(r'\S[\s\S]{0,1799}(?=\s|\Z)', block.text):
            start = block.char_start + match.start()
            if start < end and not any(start < b and a < start + len(match.group()) for a, b in blocked):
                windows.append((start, match.group()))
    selected, used = [], 0
    excluded = []
    for quote in exclude_quotes:
        if quote:
            excluded.extend((m.start(), m.end()) for m in re.finditer(re.escape(quote), text))

    def add(item):
        nonlocal used
        start, passage = item
        stop = start + len(passage)
        if used + len(passage) + 2 > max_chars or any(start < b and a < stop for a, b in [*excluded, *blocked]):
            return False
        if any(passage == s or start < p + len(s) and p < stop for p, s in selected):
            return False
        selected.append(item)
        used += len(passage) + 2
        return True

    if windows and not targeted:
        add((windows[0][0], text[windows[0][0]:min(windows[0][0]+1600, end)]))
    roles = ['taxonomy classification categories architectures methods',
             'normal training images samples feature modeling representation',
             'anomaly scoring distance discrepancy likelihood reconstruction residual',
             'memory distribution reconstruction knowledge distillation mechanism',
             'limitations domain shift deployment normal variation']
    if targeted:
        roles = list(focus_queries) or roles
    for role in roles:
        ranked = sorted(windows, key=lambda item: (-reader._score(item[1], role), item[0]))
        for item in ranked:
            if reader._score(item[1], role) > 0 and add(item):
                break
    for item in sorted(windows, key=lambda item: (-sum(reader._score(item[1], q) for q in [*roles, *queries]), item[0])):
        add(item)
    return [{'passage_id': f'p_{start}', 'text': passage} for start, passage in sorted(selected)]
