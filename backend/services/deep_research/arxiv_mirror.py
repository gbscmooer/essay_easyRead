"""arXiv 本地镜像：OAI-PMH 抓取元数据 → SQLite（FTS5 字面检索）+ bge-m3 向量（语义检索）。

不需要任何 key。抓取遵守 arXiv OAI-PMH 流控：请求间隔不少于 5 秒，503 按 Retry-After 等待；
断点续传（resumptionToken 与进度存库），抓完后按上次 datestamp 增量更新。
检索：字面、语义两个通道各自提名候选，不做分数融合，交给召回引擎后面的重排模型统一排序。

命令：
  python -m backend.services.deep_research.arxiv_mirror harvest [--sets cs,stat]
  python -m backend.services.deep_research.arxiv_mirror embed
  python -m backend.services.deep_research.arxiv_mirror search "query"
  python -m backend.services.deep_research.arxiv_mirror stats
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "arxiv_mirror"
OAI_BASE = "https://oaipmh.arxiv.org/oai"
USER_AGENT = "essay_read-arxiv-mirror/1.0 (academic thesis project; metadata harvest)"
REQUEST_INTERVAL = 5.0          # 秒；arXiv 要求批量抓取放慢速度
MAX_RETRY_AFTER = 600.0
DEFAULT_SETS = ("cs", "stat")
# arXiv 全为英文，用英文 bge-base（M3 芯片上半精度约 56 篇/秒，百万篇约 5 小时；
# bge-large 约慢 3 倍，bge-m3 多语言用不上）
EMBED_MODEL = "BAAI/bge-base-en-v1.5"
EMBED_DIM = 768
EMBED_MAX_TOKENS = 256          # 标题+摘要绝大多数在 256 token 内；截断换速度
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "  # bge v1.5 检索查询前缀

_NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "arxiv": "http://arxiv.org/OAI/arXiv/"}
_STOP = frozenset("a an and are as at be by for from how in into is it of on or that the this to "
                  "what which with why can do does using use based via".split())

Encoder = Callable[[list[str]], "object"]  # 文本 → (n, dim) 归一化向量（numpy）


# ---------- 存储 ----------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    rowid INTEGER PRIMARY KEY,
    arxiv_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '',
    categories TEXT NOT NULL DEFAULT '',
    year INTEGER,
    doi TEXT,
    journal_ref TEXT,
    datestamp TEXT,
    vec_row INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract, content='papers', content_rowid='rowid', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract) VALUES (new.rowid, new.title, new.abstract);
END;
CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract) VALUES ('delete', old.rowid, old.title, old.abstract);
END;
CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE OF title, abstract ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract) VALUES ('delete', old.rowid, old.title, old.abstract);
    INSERT INTO papers_fts(rowid, title, abstract) VALUES (new.rowid, new.title, new.abstract);
END;
CREATE INDEX IF NOT EXISTS papers_vec ON papers(vec_row);
CREATE INDEX IF NOT EXISTS papers_doi ON papers(doi COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(root: Path | str = DEFAULT_ROOT) -> sqlite3.Connection:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "arxiv.sqlite", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_state(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM state WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO state(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def year_from_id(arxiv_id: str) -> int | None:
    """首次提交年份取自编号（OAI 的 created 字段对旧论文不可靠）：2301.00001 → 2023，cs/9912001 → 1999。"""
    m = re.match(r"^(\d{2})(\d{2})\.\d{4,5}$", arxiv_id) or re.match(r"^[a-z-]+(?:\.[A-Z]{2})?/(\d{2})(\d{2})\d{3}$", arxiv_id)
    if not m:
        return None
    yy = int(m.group(1))
    return 1900 + yy if yy >= 91 else 2000 + yy


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_records(xml_text: str) -> tuple[list[dict], str | None, str | None]:
    """一页 ListRecords → (记录, resumptionToken, OAI 错误码)。已删除的记录跳过。"""
    root = ET.fromstring(xml_text)
    error = root.find("oai:error", _NS)
    if error is not None:
        return [], None, error.get("code") or "error"
    out = []
    for rec in root.iterfind(".//oai:record", _NS):
        header = rec.find("oai:header", _NS)
        if header is None or header.get("status") == "deleted":
            continue
        meta = rec.find("oai:metadata/arxiv:arXiv", _NS)
        if meta is None:
            continue
        arxiv_id = _clean(meta.findtext("arxiv:id", "", _NS))
        title = _clean(meta.findtext("arxiv:title", "", _NS))
        if not arxiv_id or not title:
            continue
        authors = []
        for a in meta.iterfind("arxiv:authors/arxiv:author", _NS):
            name = " ".join(x for x in (_clean(a.findtext("arxiv:forenames", "", _NS)),
                                        _clean(a.findtext("arxiv:keyname", "", _NS))) if x)
            if name:
                authors.append(name)
        out.append({
            "arxiv_id": arxiv_id, "title": title,
            "abstract": _clean(meta.findtext("arxiv:abstract", "", _NS)),
            "authors": ", ".join(authors),
            "categories": _clean(meta.findtext("arxiv:categories", "", _NS)),
            "year": year_from_id(arxiv_id),
            "doi": _clean(meta.findtext("arxiv:doi", "", _NS)) or None,
            "journal_ref": _clean(meta.findtext("arxiv:journal-ref", "", _NS)) or None,
            "datestamp": _clean(header.findtext("oai:datestamp", "", _NS)),
        })
    token_el = root.find(".//oai:resumptionToken", _NS)
    token = _clean(token_el.text) if token_el is not None and token_el.text else None
    return out, token, None


def upsert(conn: sqlite3.Connection, records: list[dict]) -> None:
    """按 arxiv_id 插入或更新；摘要或标题变化时清掉向量行号，等待重新嵌入。"""
    conn.executemany(
        """INSERT INTO papers(arxiv_id, title, abstract, authors, categories, year, doi, journal_ref, datestamp)
           VALUES (:arxiv_id, :title, :abstract, :authors, :categories, :year, :doi, :journal_ref, :datestamp)
           ON CONFLICT(arxiv_id) DO UPDATE SET
             vec_row = CASE WHEN papers.title = excluded.title AND papers.abstract = excluded.abstract
                            THEN papers.vec_row ELSE NULL END,
             title = excluded.title, abstract = excluded.abstract, authors = excluded.authors,
             categories = excluded.categories, year = excluded.year, doi = excluded.doi,
             journal_ref = excluded.journal_ref, datestamp = excluded.datestamp""",
        records)


# ---------- 抓取 ----------

def harvest(conn: sqlite3.Connection, set_spec: str, *, fetch: Callable[[dict], tuple[int, dict, str]] | None = None,
            sleep: Callable[[float], None] = time.sleep, max_pages: int | None = None,
            interval: float = REQUEST_INTERVAL) -> dict:
    """抓取一个分类集合；可中断，再次运行从保存的 resumptionToken 续传，抓完后转为增量。

    fetch(params) -> (status, headers, body)，测试时注入；缺省走 httpx。
    """
    if fetch is None:
        import httpx

        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120.0)

        def fetch(params: dict) -> tuple[int, dict, str]:
            try:
                r = client.get(OAI_BASE, params=params)
            except httpx.TransportError as exc:
                raise ConnectionError(str(exc)) from exc
            return r.status_code, dict(r.headers), r.text

    token_key, since_key = f"harvest:{set_spec}:token", f"harvest:{set_spec}:since"
    token = _get_state(conn, token_key)
    since = _get_state(conn, since_key)
    newest = _get_state(conn, f"harvest:{set_spec}:newest") or since or ""
    pages = records = 0
    failures = 0
    while True:
        if token:
            params = {"verb": "ListRecords", "resumptionToken": token}
        else:
            params = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": set_spec}
            if since:
                params["from"] = since
        try:
            status, headers, body = fetch(params)
        except OSError as exc:  # 连接被重置、SSL 握手失败等；httpx 的网络异常也归此类
            status, headers, body = -1, {}, ""
            logger.warning("OAI request failed: %s", exc)
        if status == 503:
            wait = min(float(headers.get("Retry-After") or headers.get("retry-after") or 60), MAX_RETRY_AFTER)
            logger.info("OAI 503, waiting %.0fs", wait)
            sleep(wait)
            continue
        if status != 200:
            failures += 1
            if failures > 8:
                raise RuntimeError(f"OAI-PMH HTTP {status} after retries")
            sleep(min(interval * 2 ** failures, 300.0))
            continue
        failures = 0
        rows, next_token, error = parse_records(body)
        if error == "badResumptionToken":
            # 令牌过期：从断点日期重新列（记录按 arxiv_id 幂等写入）
            token = None
            _set_state(conn, token_key, None)
            since = newest or since
            conn.commit()
            continue
        if error and error != "noRecordsMatch":
            raise RuntimeError(f"OAI-PMH error: {error}")
        if rows:
            upsert(conn, rows)
            newest = max([newest, *(r["datestamp"] for r in rows)])
        token = next_token
        _set_state(conn, token_key, token)
        _set_state(conn, f"harvest:{set_spec}:newest", newest or None)
        if not token:
            _set_state(conn, since_key, newest or since)
        conn.commit()
        pages += 1
        records += len(rows)
        if pages % 20 == 0:
            logger.info("%s: %d pages, %d records", set_spec, pages, records)
        if not token or (max_pages and pages >= max_pages):
            break
        sleep(interval)
    return {"set": set_spec, "pages": pages, "records": records, "complete": token is None,
            "newest_datestamp": newest}


# ---------- 向量 ----------

def _vector_paths(root: Path) -> Path:
    return root / "vectors.f16"


def load_encoder(model_name: str = EMBED_MODEL, *, max_tokens: int = EMBED_MAX_TOKENS) -> Encoder:
    from sentence_transformers import SentenceTransformer
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    try:  # 先只用本地缓存，避免联网检查卡住
        model = SentenceTransformer(model_name, device=device, local_files_only=True)
    except Exception:  # noqa: BLE001
        model = SentenceTransformer(model_name, device=device)
    model.max_seq_length = max_tokens
    if device == "mps":
        model.half()  # 结果与 fp32 余弦一致（实测 1.0000），速度约 +20%

    def encode(texts: list[str]):
        return model.encode(texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True,
                            show_progress_bar=False)
    return encode


def embed(conn: sqlite3.Connection, root: Path | str = DEFAULT_ROOT, *, encoder: Encoder | None = None,
          batch: int = 256, limit: int | None = None, dim: int = EMBED_DIM) -> dict:
    """给没有向量的论文算向量，追加到 vectors.f16（float16，行号存回 papers.vec_row）。可中断续跑。"""
    import numpy as np

    root = Path(root)
    path = _vector_paths(root)
    row_bytes = dim * 2
    # 已提交的向量行数记在 state；文件比它长说明上次在提交前中断，截掉多写的部分
    used = int(_get_state(conn, "vectors:count") or 0)
    with open(path, "ab"):
        pass
    if path.stat().st_size != used * row_bytes:
        with open(path, "r+b") as f:
            f.truncate(used * row_bytes)
    encoder = encoder or load_encoder()
    done = 0
    started = time.monotonic()
    with open(path, "ab") as f:
        while limit is None or done < limit:
            take = batch if limit is None else min(batch, limit - done)
            rows = conn.execute("SELECT rowid, title, abstract FROM papers WHERE vec_row IS NULL "
                                "ORDER BY rowid LIMIT ?", (take,)).fetchall()
            if not rows:
                break
            vecs = np.asarray(encoder([f"{t}. {a}" for _, t, a in rows]), dtype=np.float16)
            if vecs.shape != (len(rows), dim):
                raise ValueError(f"encoder returned shape {vecs.shape}, expected ({len(rows)}, {dim})")
            f.write(vecs.tobytes())
            f.flush()
            conn.executemany("UPDATE papers SET vec_row = ? WHERE rowid = ?",
                             [(used + i, rowid) for i, (rowid, _, _) in enumerate(rows)])
            used += len(rows)
            _set_state(conn, "vectors:count", str(used))
            conn.commit()
            done += len(rows)
            if done % (batch * 40) == 0:
                rate = done / max(time.monotonic() - started, 1e-6)
                logger.info("embedded %d (%.0f/s)", done, rate)
    return {"embedded": done, "vectors": used}


# ---------- 检索 ----------

def fts_query(text: str, *, mode: str = "and") -> str:
    terms = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1]
    if not terms:
        return ""
    return f" {'AND' if mode == 'and' else 'OR'} ".join(f'"{t}"' for t in dict.fromkeys(terms))


def _title_key(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


class ArxivMirror:
    """本地 arXiv 检索：lexical（FTS5 BM25，标题权重 4）与 dense（bge-m3 余弦）。"""

    def __init__(self, root: Path | str = DEFAULT_ROOT, *, encoder: Encoder | None = None):
        self.root = Path(root)
        self._conn: sqlite3.Connection | None = None
        self._encoder = encoder
        self._matrix = None
        self._vec_ids = None
        self._lock = threading.Lock()  # 检索在线程池里跑，共用一个连接与向量矩阵

    @classmethod
    def available(cls, root: Path | str = DEFAULT_ROOT, *, required_set: str = "cs") -> bool:
        """抓取完成过一次（有增量起点）才算可用；抓取进行中的半成品不接管在线检索。"""
        db = Path(root) / "arxiv.sqlite"
        if not db.exists():
            return False
        try:
            conn = sqlite3.connect(db)
            done = conn.execute("SELECT value FROM state WHERE key = ?",
                                (f"harvest:{required_set}:since",)).fetchone()
            return bool(done) and conn.execute("SELECT EXISTS(SELECT 1 FROM papers)").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def dense_ready(self, min_share: float = 0.95) -> bool:
        """向量覆盖不足（嵌入未跑完）时不启用语义通道，避免只在部分论文里检索。"""
        total, embedded = self.conn.execute("SELECT COUNT(*), COUNT(vec_row) FROM papers").fetchone()
        return total > 0 and embedded / total >= min_share

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = connect(self.root)
        return self._conn

    def _rows(self, rowids: list[int]) -> dict[int, dict]:
        if not rowids:
            return {}
        q = ",".join("?" * len(rowids))
        cur = self.conn.execute(f"SELECT rowid, arxiv_id, title, abstract, authors, categories, year, doi "
                                f"FROM papers WHERE rowid IN ({q})", rowids)
        cols = ["rowid", "arxiv_id", "title", "abstract", "authors", "categories", "year", "doi"]
        return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}

    def lexical(self, query: str, limit: int) -> list[dict]:
        """先全部词同时出现（AND），不足一半时用 OR 补足，均按 BM25 排序。"""
        found: list[int] = []
        for mode in ("and", "or"):
            expr = fts_query(query, mode=mode)
            if not expr:
                break
            cur = self.conn.execute(
                "SELECT rowid FROM papers_fts WHERE papers_fts MATCH ? ORDER BY bm25(papers_fts, 4.0, 1.0) LIMIT ?",
                (expr, limit))
            for (rowid,) in cur.fetchall():
                if rowid not in found:
                    found.append(rowid)
            if len(found) >= limit / 2:
                break
        rows = self._rows(found[:limit])
        return [rows[r] for r in found[:limit] if r in rows]

    def _load_vectors(self) -> bool:
        if self._matrix is not None:
            return True
        import numpy as np

        path = _vector_paths(self.root)
        n = int(_get_state(self.conn, "vectors:count") or 0)
        if not path.exists() or n == 0:
            return False
        data = np.memmap(path, dtype=np.float16, mode="r", shape=(n, EMBED_DIM))
        ids = np.full(n, -1, dtype=np.int64)
        for rowid, vec_row in self.conn.execute("SELECT rowid, vec_row FROM papers WHERE vec_row IS NOT NULL"):
            ids[vec_row] = rowid
        try:
            import torch

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            self._matrix = torch.from_numpy(np.array(data)).to(device)
        except Exception:  # noqa: BLE001 — 无 torch 时退回 numpy 分块
            self._matrix = data
        self._vec_ids = ids
        return True

    def dense(self, query: str, limit: int) -> list[dict]:
        import numpy as np

        if not self._load_vectors():
            return []
        if self._encoder is None:
            self._encoder = load_encoder()
        qvec = np.asarray(self._encoder([QUERY_INSTRUCTION + query]), dtype=np.float32)[0]
        matrix = self._matrix
        if hasattr(matrix, "device"):  # torch
            import torch

            q = torch.from_numpy(qvec.astype(np.float16)).to(matrix.device)
            scores = (matrix @ q).float()
            top = torch.topk(scores, min(limit * 2, scores.shape[0])).indices.cpu().numpy()
        else:
            scores = np.concatenate([np.asarray(matrix[i:i + 65536], dtype=np.float32) @ qvec
                                     for i in range(0, matrix.shape[0], 65536)])
            k = min(limit * 2, scores.shape[0])
            top = np.argpartition(-scores, k - 1)[:k]
            top = top[np.argsort(-scores[top])]
        rowids: list[int] = []
        for vec_row in top:
            rowid = int(self._vec_ids[vec_row])
            if rowid >= 0 and rowid not in rowids:  # 被更新过的论文旧向量行号为 -1
                rowids.append(rowid)
            if len(rowids) == limit:
                break
        rows = self._rows(rowids)
        return [rows[r] for r in rowids if r in rows]

    def match(self, items: list[tuple[str | None, str]]) -> list[dict | None]:
        """按 DOI 或规范化题名精确匹配镜像论文；用于给只有题名的 Scopus 论文补摘要与 arXiv 编号。"""
        out: list[dict | None] = []
        with self._lock:
            for doi, title in items:
                row = None
                if doi:
                    hit = self.conn.execute("SELECT rowid FROM papers WHERE doi = ? COLLATE NOCASE LIMIT 1",
                                            (doi,)).fetchone()
                    row = self._rows([hit[0]]).get(hit[0]) if hit else None
                expr = fts_query(title)
                if row is None and expr:
                    cur = self.conn.execute("SELECT rowid FROM papers_fts WHERE papers_fts MATCH ? "
                                            "ORDER BY bm25(papers_fts, 4.0, 1.0) LIMIT 5", (expr,))
                    key = _title_key(title)
                    row = next((r for r in self._rows([i for (i,) in cur.fetchall()]).values()
                                if _title_key(r["title"]) == key), None)
                out.append(row if row and (row.get("abstract") or "").strip() else None)
        return out

    def as_search_one(self):
        """召回引擎的 search_one：arxiv 源走本地镜像；两个通道各提名 limit 篇，去重后返回。"""
        from ...models.recall import RecallSource, build_recall_candidate

        async def search_one(source: str, keyword: str, limit: int, order: str) -> list:
            import asyncio

            def both():
                with self._lock:
                    dense = self.dense(keyword, limit) if self.dense_ready() else []
                    return self.lexical(keyword, limit), dense

            lexical, dense = await asyncio.to_thread(both)
            out, seen = [], set()
            for channel, rows in (("lexical", lexical), ("dense", dense)):
                for rank, row in enumerate(rows):
                    if row["arxiv_id"] in seen:
                        continue
                    seen.add(row["arxiv_id"])
                    cand = build_recall_candidate(
                        title=row["title"], arxiv_id=row["arxiv_id"], doi=row.get("doi"), year=row.get("year"),
                        authors=row.get("authors"), abstract=row.get("abstract"),
                        pdf_url=f"https://arxiv.org/pdf/{row['arxiv_id']}", citation_count=0,
                        source=RecallSource.SEARCH,
                        provenance=["provider:arxiv", "index:local_mirror", f"channel:{channel}", f"rank:{rank}"])
                    if cand:
                        out.append(cand)
            return out
        return search_one


_mirror_singleton: ArxivMirror | None = None


def default_mirror() -> ArxivMirror | None:
    """本地镜像已建好时返回单例；否则 None（召回退回 arXiv 在线 API）。

    环境变量 ARXIV_MIRROR=off 强制用在线 API（对照实验时防止镜像中途接管）；
    ARXIV_MIRROR=on 在首轮抓取未完成时也启用已有部分（评测用）。
    """
    global _mirror_singleton
    mode = os.environ.get("ARXIV_MIRROR", "auto").lower()
    if mode == "off":
        return None
    ready = (DEFAULT_ROOT / "arxiv.sqlite").exists() if mode == "on" else ArxivMirror.available()
    if _mirror_singleton is None and ready:
        _mirror_singleton = ArxivMirror()
    return _mirror_singleton


# ---------- 命令行 ----------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = parser.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest")
    h.add_argument("--sets", default=",".join(DEFAULT_SETS))
    h.add_argument("--max-pages", type=int, default=0)
    e = sub.add_parser("embed")
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--batch", type=int, default=256)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--channel", choices=("lexical", "dense", "both"), default="both")
    sub.add_parser("stats")
    args = parser.parse_args()
    root = Path(args.root)
    if args.cmd == "harvest":
        conn = connect(root)
        for set_spec in args.sets.split(","):
            print(harvest(conn, set_spec.strip(), max_pages=args.max_pages or None), flush=True)
    elif args.cmd == "embed":
        conn = connect(root)
        print(embed(conn, root, limit=args.limit or None, batch=args.batch), flush=True)
    elif args.cmd == "search":
        mirror = ArxivMirror(root)
        for channel in (("lexical", "dense") if args.channel == "both" else (args.channel,)):
            print(f"== {channel}")
            for row in getattr(mirror, channel)(args.query, args.limit):
                print(f"  {row['arxiv_id']:>16}  {row['year']}  {row['title'][:100]}")
    else:
        conn = connect(root)
        total, vec = conn.execute("SELECT COUNT(*), COUNT(vec_row) FROM papers").fetchone()
        state = dict(conn.execute("SELECT key, value FROM state").fetchall())
        print({"papers": total, "embedded": vec,
               "state": {k: (v[:40] if v else v) for k, v in state.items()}})


if __name__ == "__main__":
    main()
