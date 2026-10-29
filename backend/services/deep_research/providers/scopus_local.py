"""Scopus 本地缓存与本地检索（与 Scopus Search / Abstract Retrieval 返回同样的 JSON）。

用途：改召回以外的环节（筛选、精读、作答等）时，同一批题反复重跑完整八阶段流程，召回不再消耗 Scopus 检索额度
（周配额 2 万次，一次八阶段召回约 15~30 次）。先 record 跑一遍，之后同一批题用 local 重跑：
- record：照常调 Scopus，每次响应写入本地库；同一请求再出现时直接用缓存；检索结果里的论文另存一份供本地检索。
- local：不调 Scopus。缓存里有同一请求就原样返回；没有时在本地论文库里检索并按 Scopus 格式返回——
  TITLE-ABS-KEY 无 OR 时近似 Scopus 的宽松 AND，有 OR 时按相关度（bm25，porter 词干化近似 Scopus 的词形归并）；
  TITLE("…") 按题名、DOI("…") OR … 按 DOI；sort=-citedby-count 时按被引降序；参考文献列表只用缓存。
本地检索只能找到以前见过的论文，适合同一批题的对照与消融，不代替真实召回的覆盖面。

开关：环境变量 ESSAY_SCOPUS_CACHE=off（默认）| record | local；库文件默认 data/cache/scopus_local.sqlite
（ESSAY_SCOPUS_CACHE_DB 可改）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

_STOP = frozenset("a an and are as at be by for from in into is of on or the to with without via using based "
                  "how what which when where why do does did can between among during after before".split())
_LOCK = threading.Lock()
_INSTANCE: "ScopusLocal | None" = None


def mode() -> str:
    m = os.environ.get("ESSAY_SCOPUS_CACHE", "off").strip().lower()
    return m if m in ("record", "local") else "off"


def default_path() -> Path:
    env = os.environ.get("ESSAY_SCOPUS_CACHE_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4] / "data" / "cache" / "scopus_local.sqlite"


def active() -> "ScopusLocal | None":
    """当前生效的本地库（mode 为 off 时返回 None）。"""
    global _INSTANCE
    if mode() == "off":
        return None
    with _LOCK:
        if _INSTANCE is None or _INSTANCE.path != default_path():
            _INSTANCE = ScopusLocal(default_path())
    return _INSTANCE


def _request_key(path: str, params: dict | None) -> str:
    clean = {k: str(v) for k, v in (params or {}).items() if k.lower() not in ("apikey", "insttoken")}
    return hashlib.sha1(json.dumps([path, sorted(clean.items())], ensure_ascii=False).encode()).hexdigest()


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


class ScopusLocal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS responses(key TEXT PRIMARY KEY, path TEXT, params TEXT, body TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS papers(eid TEXT PRIMARY KEY, doi TEXT, title TEXT, ntitle TEXT, abstract TEXT,
                                              year INTEGER, cited INTEGER, entry TEXT);
            CREATE INDEX IF NOT EXISTS papers_doi ON papers(doi);
            CREATE INDEX IF NOT EXISTS papers_ntitle ON papers(ntitle);
            CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(eid UNINDEXED, title, abstract,
                                                                     tokenize="porter unicode61");
        """)
        self.hits = self.misses = self.served = 0

    # ── 缓存 ────────────────────────────────────────────────────────────
    def get(self, path: str, params: dict | None) -> dict | None:
        with _LOCK:
            row = self.conn.execute("SELECT body FROM responses WHERE key=?", (_request_key(path, params),)).fetchone()
        if row:
            self.hits += 1
            return json.loads(row[0])
        self.misses += 1
        return None

    def put(self, path: str, params: dict | None, body: dict) -> None:
        if not isinstance(body, dict):
            return
        with _LOCK:
            self.conn.execute("INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?)",
                              (_request_key(path, params), path, json.dumps(params or {}, ensure_ascii=False),
                               json.dumps(body, ensure_ascii=False), time.time()))
            self.conn.commit()
        if path.startswith("/content/search/scopus"):
            self.add_entries(((body.get("search-results") or {}).get("entry")) or [])

    def add_entries(self, entries: list[dict]) -> int:
        """检索结果条目存入本地论文库（同一 eid 已有摘要时不被无摘要的版本覆盖）。"""
        n = 0
        with _LOCK:
            for e in entries:
                if not isinstance(e, dict) or e.get("error"):
                    continue
                eid = str(e.get("eid") or e.get("dc:identifier") or e.get("prism:doi") or "")
                title = str(e.get("dc:title") or "").strip()
                if not eid or not title:
                    continue
                abstract = str(e.get("dc:description") or "").strip()
                old = self.conn.execute("SELECT abstract, entry FROM papers WHERE eid=?", (eid,)).fetchone()
                if old and old[0] and not abstract:
                    continue
                year = str(e.get("prism:coverDate") or "")[:4]
                doi = str(e.get("prism:doi") or "").lower() or None
                self.conn.execute("INSERT OR REPLACE INTO papers VALUES (?,?,?,?,?,?,?,?)",
                                  (eid, doi, title, _norm_title(title), abstract,
                                   int(year) if year.isdigit() else None, int(e.get("citedby-count") or 0),
                                   json.dumps(e, ensure_ascii=False)))
                self.conn.execute("DELETE FROM papers_fts WHERE eid=?", (eid,))
                self.conn.execute("INSERT INTO papers_fts VALUES (?,?,?)", (eid, title, abstract))
                n += 1
            self.conn.commit()
        return n

    def stats(self) -> dict:
        with _LOCK:
            return {"responses": self.conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0],
                    "papers": self.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
                    "with_abstract": self.conn.execute(
                        "SELECT COUNT(*) FROM papers WHERE abstract <> ''").fetchone()[0],
                    "hits": self.hits, "misses": self.misses, "served_locally": self.served}

    # ── 本地检索（模仿 Scopus） ──────────────────────────────────────────
    def serve(self, path: str, params: dict | None) -> dict | None:
        """缓存未命中时在本地库里答：检索走本地全文索引，其余（参考文献列表等）返回 None。"""
        if not path.startswith("/content/search/scopus"):
            return None
        self.served += 1
        p = params or {}
        query = str(p.get("query") or "")
        count, start = int(p.get("count") or 25), int(p.get("start") or 0)
        rows = self._match(query, sort=str(p.get("sort") or "-relevancy"), limit=start + count)
        entries = [json.loads(r) for r in rows[start:start + count]]
        return {"search-results": {"opensearch:totalResults": str(len(rows)), "entry": entries}}

    def _match(self, query: str, *, sort: str, limit: int) -> list[str]:
        dois = re.findall(r'DOI\("([^"]+)"\)', query)
        with _LOCK:
            if dois:
                marks = ",".join("?" * len(dois))
                return [r[0] for r in self.conn.execute(
                    f"SELECT entry FROM papers WHERE doi IN ({marks})", [d.lower() for d in dois])]
            m = re.search(r'TITLE\("([^"]+)"\)', query)
            if m:
                rows = self.conn.execute("SELECT entry FROM papers WHERE ntitle=? LIMIT ?",
                                         (_norm_title(m.group(1)), limit)).fetchall()
                if rows:
                    return [r[0] for r in rows]
                fts, column = self._fts_query(m.group(1), any_term=False), "title"
            else:
                m = re.search(r"TITLE-ABS-KEY\((.*?)\)(?:\s+AND\s+DOCTYPE|\s*$)", query)
                body = m.group(1) if m else query
                fts, column = self._fts_query(body, any_term=" OR " in body), None
            if not fts:
                return []
            target = f"{column} : ({fts})" if column else fts
            order = "p.cited DESC" if sort == "-citedby-count" else "bm25(papers_fts)"
            try:
                return [r[0] for r in self.conn.execute(
                    f"SELECT p.entry FROM papers_fts JOIN papers p ON p.eid = papers_fts.eid "
                    f"WHERE papers_fts MATCH ? ORDER BY {order} LIMIT ?", (target, max(limit, 1)))]
            except sqlite3.OperationalError:
                return []

    @staticmethod
    def _fts_query(text: str, *, any_term: bool) -> str:
        words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", text)
                 if w.upper() not in ("AND", "OR", "NOT") and w.lower() not in _STOP and len(w) > 1]
        words = list(dict.fromkeys(w.lower() for w in words))
        if not words:
            return ""
        quoted = [f'"{w}"' for w in words]
        return " OR ".join(quoted) if any_term else " AND ".join(quoted)


def seed_from_papers(papers: list[dict]) -> int:
    """把已保存运行里的论文（召回结果的 dict 格式）补进本地论文库，转成 Scopus 检索条目的形状。"""
    local = active() or ScopusLocal(default_path())
    entries = []
    for p in papers:
        if not p.get("scopus_id") and not p.get("doi"):
            continue
        sid = str(p.get("scopus_id") or "").replace("SCOPUS_ID:", "")
        entries.append({
            "eid": f"2-s2.0-{sid}" if sid else f"doi:{p.get('doi')}",
            "dc:identifier": f"SCOPUS_ID:{sid}" if sid else None,
            "dc:title": p.get("title"), "prism:doi": p.get("doi"),
            "prism:coverDate": f"{p['year']}-01-01" if p.get("year") else None,
            "citedby-count": str(p.get("citation_count") or 0), "dc:description": p.get("abstract") or "",
            "prism:publicationName": p.get("venue_name") or "", "subtypeDescription": p.get("publication_type"),
            "dc:creator": (p.get("authors") or [""])[0] if isinstance(p.get("authors"), list) else "",
        })
    return local.add_entries(entries)
