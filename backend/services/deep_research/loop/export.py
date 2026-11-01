"""研究结果导出：答案 + 参考文献摘录 + 选中论文清单（Markdown / 自包含 HTML）。"""

from __future__ import annotations

import html
import re


def render_markdown(result: dict) -> str:
    answer = result.get("answer") or {}
    papers = result.get("papers") or []
    lines = [f"# {result.get('question') or '研究结果'}", ""]
    if answer.get("text"):
        lines += ["## 回答", "", answer["text"].strip(), ""]
    refs = answer.get("references") or []
    if refs:
        lines += ["## 参考文献", ""]
        for r in refs:
            year = f" ({r['year']})" if r.get("year") else ""
            lines.append(f"[{r.get('n')}] {r.get('title') or r.get('key')}{year}")
            for ex in r.get("excerpts") or ([r["excerpt"]] if r.get("excerpt") else []):
                lines.append(f"    > {ex}")
        lines.append("")
    if papers:
        lines += [f"## 选中论文（{len(papers)} 篇，按相关性）", ""]
        for p in papers:
            year = f" ({p['year']})" if p.get("year") else ""
            how = {"search": "检索", "refine": "补检", "expand": "引用展开", "seed": "母论文", "reuse": "复用"}.get(
                p.get("origin"), p.get("origin") or "")
            lines.append(f"- {p.get('title')}{year} · 判定 {p.get('label')} · {how}"
                         + (f"（{p['via']}）" if p.get("via") and p.get("origin") != "search" else ""))
        lines.append("")
    queries = result.get("queries") or []
    if queries:
        lines += ["## 检索式", "", *[f"- {q}" for q in queries], ""]
    crawl = result.get("crawl") or {}
    if crawl:
        lines += ["## 过程", "",
                  f"候选 {crawl.get('candidates', 0)} 篇，选中 {crawl.get('selected', 0)} 篇，"
                  f"展开 {crawl.get('expanded', 0)} 篇，停止原因：{crawl.get('stop_reason') or '—'}。", ""]
    return "\n".join(lines)


def _md_to_html(md: str) -> str:
    out, in_list = [], False
    for raw in md.splitlines():
        line = html.escape(raw)
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        if raw.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
        elif raw.startswith("## "):
            out.append(f"<h2>{line[3:]}</h2>")
        elif raw.startswith("    &gt; ") or raw.startswith("    > "):
            out.append(f"<blockquote>{line.split('&gt; ', 1)[-1]}</blockquote>")
        elif raw.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
            continue
        elif raw.strip():
            out.append(f"<p>{line}</p>")
        if in_list:
            out.insert(len(out) - 1, "</ul>")
            in_list = False
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def render_html(result: dict) -> str:
    body = _md_to_html(render_markdown(result))
    return ("<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(result.get('question') or '研究结果')}</title>"
            "<style>body{max-width:860px;margin:2rem auto;padding:0 1rem;font:16px/1.7 system-ui,sans-serif}"
            "blockquote{color:#555;border-left:3px solid #ccc;margin:.2rem 0 .2rem 1rem;padding-left:.6rem}"
            "</style></head><body>" + body + "</body></html>")
