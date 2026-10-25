"""公开人工标注数据集上的分环节评测：SYNERGY（筛选）与 LitSearch（召回）。

- SYNERGY（ASReview，26 篇系统综述的人工纳入标签）：把综述题目作为研究问题，
  候选论文的标题/摘要交给正式流程同一个筛选器 `_filter_pool`，对照人工标签
  计算查全率/查准率。筛选器三分类（接受/待定/拒绝）：严格口径只算接受，
  宽松口径算未拒绝（正式流程中待定论文会再复筛，二者之间是真实表现区间）。
  有摘要筛选阶段标签（label_abstract_screening）的数据集优先用它作金标准——
  最终纳入标签含全文阶段才排除的论文，只看摘要不可能全对。
- LitSearch（Princeton，EMNLP 2024，597 个专家审核的 ML/NLP 文献检索问题）：
  对每个问题跑正式流程的“拆解 + 目标量召回”，统计目标论文是否进入召回语料、
  是否通过筛选、在输出中的名次。两种语料：
  · local（默认）：用 `local_search.LocalCorpusIndex` 在数据集自带的 64,183 篇语料里检索，
    替代联网检索、返回格式相同；同时用原问题直接 BM25 检索作零模型成本的基线；
  · online：检索真实数据源（arXiv/Scopus/OpenAlex），与原文的固定语料不可直接比较。

用法：
  python -m backend.eval.public_benchmarks synergy --dataset Nagtegaal_2019
  python -m backend.eval.public_benchmarks synergy --dataset Wahono_2015 --negatives 940
  python -m backend.eval.public_benchmarks litsearch --n 30 --specificity 0            # 本地语料
  python -m backend.eval.public_benchmarks litsearch --n 30 --specificity 0 --corpus online
数据缓存在 data/eval/public/，结果写 data/eval/public_{name}_{stamp}.json。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
from contextvars import ContextVar
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path("data/eval/public")
OUT_DIR = Path("data/eval")
SYNERGY_INDEX = "https://raw.githubusercontent.com/asreview/synergy-dataset/master/index.csv"
LITSEARCH_ROWS = ("https://datasets-server.huggingface.co/rows?dataset=princeton-nlp/LitSearch"
                  "&config=query&split=full&offset={offset}&length=100")
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=title,externalIds,year"


# ── 纯函数：采样与计分（单元测试覆盖） ──────────────────────────────────────

def norm_title(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", (text or "").lower()).split())


def sample_records(rows: list[dict], label: str, negatives: int | None, seed: int = 0) -> list[dict]:
    """全部正例 + 固定种子随机抽取的负例；negatives=None 表示全量。"""
    if negatives is None:
        return list(rows)
    pos = [r for r in rows if r[label] == "1"]
    neg = [r for r in rows if r[label] != "1"]
    rng = random.Random(seed)
    return pos + rng.sample(neg, min(negatives, len(neg)))


def _rates(tp: int, fp: int, fn: int, tn: int) -> dict:
    recall = tp / (tp + fn) if tp + fn else None
    precision = tp / (tp + fp) if tp + fp else None
    if recall is None or precision is None:
        f1 = None
    else:
        f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "recall": None if recall is None else round(recall, 3),
            "precision": None if precision is None else round(precision, 3),
            "f1": None if f1 is None else round(f1, 3),
            "specificity": round(tn / (tn + fp), 3) if tn + fp else None,
            # 筛掉的比例：人工只需复核剩余部分
            "screened_out": round((fn + tn) / (tp + fp + fn + tn), 3) if tp + fp + fn + tn else None}


def score_screening(verdicts: dict[str, str], gold: dict[str, bool]) -> dict:
    """verdicts: key → confirmed/title_confirmed/unknown/rejected；gold: key → 人工是否纳入。"""
    out = {}
    for mode, accept in (("strict", {"confirmed", "title_confirmed"}),
                         ("lenient", {"confirmed", "title_confirmed", "unknown"})):
        tp = fp = fn = tn = 0
        for key, included in gold.items():
            predicted = verdicts.get(key, "unknown") in accept
            tp += predicted and included
            fp += predicted and not included
            fn += (not predicted) and included
            tn += (not predicted) and not included
        out[mode] = _rates(tp, fp, fn, tn)
    out["verdict_counts"] = {v: list(verdicts.values()).count(v)
                             for v in ("confirmed", "title_confirmed", "unknown", "rejected")}
    return out


def match_targets(targets: list[dict], corpus: list[dict], rejected_keys: set[str] | None = None) -> list[dict]:
    """按 DOI / arXiv / 规范化标题匹配目标论文是否进入召回语料（语料已经过筛选）。

    rejected_keys 是筛选器拒绝的论文 key（doi:/arxiv: 前缀）：目标被检到却被筛掉时
    记 rejected_by_filter，区分“没检到”与“检到但误筛”。
    """
    from backend.services.paper_identity import normalize_arxiv_id, normalize_doi

    rejected_keys = rejected_keys or set()
    index: dict[str, dict] = {}
    for p in corpus:
        for ident in _identities(p.get("doi"), p.get("arxiv_id"), normalize_doi, normalize_arxiv_id):
            index.setdefault(ident, p)
        if p.get("key"):
            index.setdefault(str(p["key"]), p)
        if p.get("title"):
            index.setdefault("title:" + norm_title(p["title"]), p)
    rows = []
    for t in targets:
        ext = t.get("externalIds") or {}
        own = _identities(ext.get("DOI"), ext.get("ArXiv"), normalize_doi, normalize_arxiv_id)
        hit = next((index[k] for k in [*own, "title:" + norm_title(t.get("title") or "")] if k in index), None)
        rows.append({"corpusid": t.get("corpusid"), "title": t.get("title"),
                     "found": hit is not None,
                     "rank": _rank_of(corpus, hit),
                     "rejected_by_filter": hit is None and bool(set(own) & rejected_keys),
                     "kept": bool(hit) and hit.get("relevance_status") in ("confirmed", "title_confirmed"),
                     "matched_key": hit.get("key") if hit else None})
    return rows


def _rank_of(corpus: list[dict], hit: dict | None) -> int | None:
    """目标在召回输出中的名次（1 起；输出顺序即系统交给下游的相关性顺序）。"""
    if hit is None:
        return None
    return next((i for i, p in enumerate(corpus, 1) if p is hit), None)


def match_local(target_ids: list, corpus: list[dict], *, rejected_keys: set[str] | None = None,
                key_of: dict[str, str] | None = None, baseline: list[str] | None = None,
                indexable: set[str] | None = None) -> list[dict]:
    """本地语料模式：按语料 id 精确匹配。key_of 把语料 id 映射到去重 key（判断是否被筛掉）；
    baseline 是原问题直接 BM25 检索的语料 id 排序。"""
    rejected_keys = rejected_keys or set()
    key_of = key_of or {}
    by_id = {str(p.get("local_id")): p for p in corpus if p.get("local_id") is not None}
    baseline_rank = {doc_id: i for i, doc_id in enumerate(baseline or [], 1)}
    # 重排前顺序（recall_pos）：同一次运行内对照「重排 vs 不重排」
    pre_order = (sorted(corpus, key=lambda p: p["recall_pos"])
                 if corpus and all("recall_pos" in p for p in corpus) else None)
    rows = []
    for tid in map(str, target_ids):
        hit = by_id.get(tid)
        rows.append({"corpusid": tid, "title": hit.get("title") if hit else None,
                     "found": hit is not None, "rank": _rank_of(corpus, hit),
                     "recall_rank": _rank_of(pre_order, hit) if pre_order else None,
                     "kept": bool(hit) and hit.get("relevance_status") in ("confirmed", "title_confirmed"),
                     "rejected_by_filter": hit is None and key_of.get(tid) in rejected_keys,
                     "baseline_rank": baseline_rank.get(tid),
                     # 语料记录无标题时检索系统无法返回它（LitSearch 6,760 篇空标题，含 7 篇目标）
                     "indexable": indexable is None or tid in indexable})
    return rows


def _identities(doi, arxiv, normalize_doi, normalize_arxiv_id) -> list[str]:
    """论文 key 形式的标识：doi:…、arxiv:…；arXiv 论文另有 10.48550/arxiv.* 形式的 DOI。"""
    out = []
    ndoi = normalize_doi(doi)
    narxiv = normalize_arxiv_id(arxiv) or (ndoi[len("10.48550/arxiv."):] if ndoi and ndoi.startswith("10.48550/arxiv.") else None)
    if ndoi:
        out.append(f"doi:{ndoi}")
    if narxiv:
        out += [f"arxiv:{narxiv.lower()}", f"doi:10.48550/arxiv.{narxiv.lower()}"]
    return list(dict.fromkeys(out))


def summarize_litsearch(records: list[dict]) -> dict:
    done = [r for r in records if not r.get("error")]
    targets = [t for r in done for t in r["targets"] if t.get("indexable", True)]

    def avg(values):
        values = list(values)
        return round(sum(values) / len(values), 3) if values else None

    return {
        "queries": len(records), "completed": len(done), "errors": len(records) - len(done),
        "targets": len(targets),
        "targets_unindexable": sum(not t.get("indexable", True) for r in done for t in r["targets"]),
        "target_recall_corpus": avg(t["found"] for t in targets),
        "target_recall_confirmed": avg(t["kept"] for t in targets),
        "target_rejected_by_filter": sum(t.get("rejected_by_filter", False) for t in targets),
        "query_any_found": avg(any(t["found"] for t in r["targets"]) for r in done),
        **{f"system_recall@{k}": avg(bool(t.get("rank")) and t["rank"] <= k for t in targets) for k in (5, 20)},
        **({f"norerank_recall@{k}": avg(bool(t.get("recall_rank")) and t["recall_rank"] <= k for t in targets)
            for k in (5, 20)} if any(t.get("recall_rank") for t in targets) else {}),
        **({f"bm25_recall@{k}": avg(bool(t.get("baseline_rank")) and t["baseline_rank"] <= k for t in targets)
            for k in (5, 20, 200)} if any("baseline_rank" in t for t in targets) else {}),
        "median_corpus": sorted(r["corpus_size"] for r in done)[len(done) // 2] if done else None,
        "wall_s_mean": avg(r["wall_s"] for r in done),
    }


# ── 数据下载 ─────────────────────────────────────────────────────────────────

def _get(url: str, *, timeout: float = 120.0, **kwargs):
    import httpx
    response = httpx.get(url, timeout=timeout, follow_redirects=True, **kwargs)
    response.raise_for_status()
    return response


def synergy_rows(dataset: str) -> tuple[dict, list[dict]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    index_path = CACHE_DIR / "synergy_index.csv"
    if not index_path.exists():
        index_path.write_bytes(_get(SYNERGY_INDEX).content)
    meta = next((r for r in csv.DictReader(index_path.open(encoding="utf-8")) if r["dataset_id"] == dataset), None)
    if meta is None:
        raise SystemExit(f"SYNERGY 无数据集 {dataset}")
    path = CACHE_DIR / f"synergy_{dataset}.csv"
    if not path.exists():
        path.write_bytes(_get(meta["url"]).content)
    csv.field_size_limit(10 ** 8)
    rows = [r for r in csv.DictReader(path.open(encoding="utf-8"))
            if not (r.get("duplicate_record_id") or "").strip()]
    return meta, rows


def litsearch_queries() -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "litsearch_query.json"
    if not path.exists():
        rows = []
        for offset in range(0, 600, 100):
            rows += [item["row"] for item in _get(LITSEARCH_ROWS.format(offset=offset)).json()["rows"]]
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


LITSEARCH_CORPUS = ("https://huggingface.co/datasets/princeton-nlp/LitSearch/resolve/"
                    "refs%2Fconvert%2Fparquet/corpus_clean/full/{:04d}.parquet")


def litsearch_corpus_path() -> Path:
    """LitSearch 语料（corpusid/title/abstract/citations 四列，约 66 MB）。

    按列远程读取 parquet，不下载 1.25 GB 的全文列；需要可选依赖 pyarrow + fsspec。
    """
    path = CACHE_DIR / "litsearch_corpus.jsonl"
    if path.exists():
        return path
    try:
        import fsspec
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise SystemExit("下载 LitSearch 语料需要 pyarrow 与 fsspec：pip install pyarrow fsspec aiohttp") from exc
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fs = fsspec.filesystem("https")
    tmp = path.with_suffix(".part")
    with tmp.open("w", encoding="utf-8") as out:
        for i in range(6):
            with fs.open(LITSEARCH_CORPUS.format(i), "rb", block_size=8 * 1024 * 1024) as f:
                table = pq.read_table(f, columns=["corpusid", "title", "abstract", "citations"])
            for row in table.to_pylist():
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.rename(path)
    return path


def litsearch_targets(corpusids: list[int]) -> dict[int, dict]:
    """目标论文元数据（S2 批量接口，带缓存）。"""
    import httpx
    path = CACHE_DIR / "litsearch_targets.json"
    cache = {int(k): v for k, v in json.loads(path.read_text()).items()} if path.exists() else {}
    missing = [c for c in dict.fromkeys(corpusids) if c not in cache]
    # 无 key 的 S2 公共额度经常 429：小批量 + 递增退避
    for i in range(0, len(missing), 100):
        chunk = missing[i:i + 100]
        for attempt in range(8):
            response = httpx.post(S2_BATCH, json={"ids": [f"CorpusId:{c}" for c in chunk]}, timeout=120)
            if response.status_code != 429:
                break
            time.sleep(15 * (attempt + 1))
        if response.status_code != 200:
            # 元数据拿不到的问题在评测中记为错误跳过，不中断整批
            print(f"  S2 元数据查询失败（HTTP {response.status_code}），{len(chunk)} 篇目标暂缺", flush=True)
            continue
        for cid, paper in zip(chunk, response.json()):
            cache[cid] = {"corpusid": cid, **(paper or {})}
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


# ── 评测执行 ─────────────────────────────────────────────────────────────────

_watch_scope: ContextVar[dict | None] = ContextVar("quota_watch_scope", default=None)


class QuotaWatch(logging.Handler):
    """捕获运行期间日志里的模型限流/额度上限（智谱 1308/1310、HTTP 429）。

    流程内部把限流当作单批失败降级（筛选 fail-open、规划停止），不会抛到评测层；
    评测必须自己识别，否则限流期间的结果会被当成有效数据。
    """

    MAX_RETRIES = 3
    _RESET = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*重置")
    # 限流与一般接口错误：本题作废，等待后重跑；被提供方拒绝/封禁：等待无用，停止评测
    _MARKERS = ("LLM_RATE_LIMITED", "LLM_PROVIDER_ERROR", "LLM_TIMEOUT", "'1308'", "'1310'", "使用上限")
    _FATAL = ("unusual activity", '"code":3012', "LLM_AUTH_FAILED", "LLM_FORBIDDEN", "不支持关闭思考")

    def __init__(self):
        super().__init__(logging.WARNING)
        self.hits = 0
        self.sample = ""
        self.fatal = ""
        self.reset_at: datetime | None = None

    @classmethod
    def install(cls) -> "QuotaWatch":
        watch = cls()
        logging.getLogger().addHandler(watch)
        if logging.getLogger().level > logging.WARNING:
            logging.getLogger().setLevel(logging.WARNING)
        return watch

    def clear(self) -> None:
        self.hits, self.sample = 0, ""

    @staticmethod
    def scope() -> dict:
        """在当前任务（及其子任务）内单独计数：并发跑多题时各题只看自己的限流。

        日志处理器的 emit 在产生日志的任务上下文里同步执行，ContextVar 能区分来源题目。
        """
        bucket = {"hits": 0, "sample": ""}
        _watch_scope.set(bucket)
        return bucket

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if any(m in msg for m in self._FATAL):
            self.fatal = self.fatal or msg[:300]
        if any(m in msg for m in self._MARKERS) or any(m in msg for m in self._FATAL):
            self.hits += 1
            self.sample = self.sample or msg[:300]
            bucket = _watch_scope.get()
            if bucket is not None:
                bucket["hits"] += 1
                bucket["sample"] = bucket["sample"] or msg[:300]
            found = self._RESET.search(msg)
            if found:
                self.reset_at = datetime.strptime(found.group(1), "%Y-%m-%d %H:%M:%S")

    async def wait_for_reset(self) -> None:
        """等到提示的重置时间（本地时间）后 2 分钟；没有给出时间时等 10 分钟。

        提供方拒绝/封禁（如 3012 unusual activity）等待无用：直接停止评测。
        """
        if self.fatal:
            raise SystemExit(f"模型提供方拒绝请求，评测停止（已完成的题已保存）：{self.fatal}")
        wait = 600.0
        if self.reset_at is not None:
            wait = max(60.0, (self.reset_at - datetime.now()).total_seconds() + 120)
        print(f"    等待额度重置 {wait / 60:.0f} 分钟（至 {self.reset_at or '未知'}）", flush=True)
        await asyncio.sleep(wait)
        self.reset_at = None


def _llm_cfg(thinking_effort: str, model: str = "") -> dict:
    """评测期间统一覆盖思考强度与模型（流程各阶段都经 ai_config.get_llm_config 取配置），
    不改用户设置文件。"""
    from backend.services import ai_config

    overrides = {k: v for k, v in (("thinking_effort", thinking_effort), ("model", model)) if v}
    if overrides:
        original = ai_config.get_llm_config
        ai_config.get_llm_config = lambda: {**original(), **overrides}
    cfg = ai_config.get_analysis_config()
    if not cfg.get("api_key"):
        raise SystemExit("未配置模型 API Key")
    return cfg


async def run_synergy(args) -> dict:
    from backend.services.deep_research.domain_synthesizer import _llm_json
    from backend.services.deep_research.recall_engine import _filter_pool

    meta, rows = synergy_rows(args.dataset)
    label = "label_abstract_screening" if "label_abstract_screening" in rows[0] and not args.final else "label_included"
    picked = sample_records(rows, label, args.negatives, args.seed)
    papers = [{"key": f"rec:{r['record_id']}", "title": r.get("title") or "", "abstract": r.get("abstract") or "",
               "year": int(r["year"]) if str(r.get("year") or "").isdigit() else None} for r in picked]
    question = args.question or meta["title"].strip()
    cfg = _llm_cfg(args.thinking_effort, args.model)

    async def screen_json(system: str, user: str) -> dict:
        # 与正式召回筛选同一调用方式（关闭思考、同一输出上限）
        return await _llm_json(cfg, system, user, max_tokens=1500, thinking_effort="off")

    verdicts: dict[str, str] = {}
    t0 = time.monotonic()

    async def progress(done, total):
        if done % 100 < 10:
            print(f"  筛选 {done}/{total}", flush=True)

    _, audit = await _filter_pool(papers, question, batch_size=10, llm_json=screen_json,
                                  verdict_cache=verdicts, on_progress=progress)
    gold = {f"rec:{r['record_id']}": r[label] == "1" for r in picked}
    result = {"dataset": args.dataset, "question": question, "gold_label": label,
              "records": len(picked), "positives": sum(gold.values()),
              "sampled": args.negatives is not None, "seed": args.seed,
              "model": cfg.get("model"), "wall_s": round(time.monotonic() - t0, 1),
              "fail_open_batches": audit.get("fail_open_batches"),
              "scores": score_screening(verdicts, gold)}
    if label != "label_included":
        final = {f"rec:{r['record_id']}": r["label_included"] == "1" for r in picked}
        result["scores_vs_final_inclusion"] = score_screening(verdicts, final)
    result["missed_examples"] = [
        {"title": p["title"][:160], "verdict": verdicts.get(p["key"])}
        for p in papers if gold[p["key"]] and verdicts.get(p["key"]) == "rejected"][:15]
    result["rejection_reasons"] = audit.get("rejections", [])[:30]
    return result


async def run_litsearch(args) -> dict:
    from backend.services.deep_research.explorer import _preset_for_depth, decompose_question
    from backend.services.deep_research.recall_engine import LISTWISE_TOP, run_target_recall
    from backend.services.deep_research.reranker import rerank_scores

    spec = None if args.specificity == "all" else int(args.specificity)
    queries = [q for q in litsearch_queries() if spec is None or q["specificity"] == spec]
    if args.quality is not None:
        queries = [q for q in queries if q["quality"] >= args.quality]
    if args.subset:
        # 固定子集：按子集文件列出的题（与 --specificity/--n 无关），保证各次运行同题
        wanted = [row["query"] for row in json.loads(Path(args.subset).read_text(encoding="utf-8"))["queries"]]
        by_text = {q["query"]: q for q in litsearch_queries()}
        picked = [by_text[text] for text in wanted]
    # --n 0：全量（按数据集原顺序），否则按种子抽样
    elif args.n <= 0:
        picked = queries
    else:
        picked = random.Random(args.seed).sample(queries, min(args.n, len(queries)))
    local = args.corpus == "local"
    if local:
        from backend.services.deep_research.local_search import LOCAL_SOURCE, LocalCorpusIndex

        t_index = time.monotonic()
        index = LocalCorpusIndex.from_jsonl(litsearch_corpus_path(), id_field="corpusid", name="litsearch")
        print(f"本地语料索引 {len(index)} 篇（{time.monotonic() - t_index:.1f}s）", flush=True)
        by_id = {d["id"]: d for d in index.docs}
        targets = {}
    else:
        targets = litsearch_targets([c for q in picked for c in q["corpusids"]])
    cfg = _llm_cfg(args.thinking_effort, args.model)
    preset = _preset_for_depth(args.depth)
    now_year = datetime.now(timezone.utc).year
    records = []
    out_path = OUT_DIR / f"public_litsearch_{args.corpus}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    if args.resume:
        # 断点续跑：沿用原文件，已成功完成的题跳过（出错的题重跑）
        out_path = Path(args.resume)
        records = [r for r in json.loads(out_path.read_text(encoding="utf-8"))["records"] if not r.get("error")]
        done = {r["query"] for r in records}
        picked = [q for q in picked if q["query"] not in done]
        print(f"续跑：已完成 {len(records)} 题，剩余 {len(picked)} 题", flush=True)

    async def no_backfill(_dois: list[str]) -> dict[str, str]:
        return {}

    watch = QuotaWatch.install()
    retries: dict[str, int] = {}
    i = 0
    while i < len(picked):
        q = picked[i]
        i += 1
        watch.clear()
        print(f"[{i}/{len(picked)}] {q['query'][:100]}", flush=True)
        t0 = time.monotonic()
        rec = {"query": q["query"], "query_set": q["query_set"], "specificity": q["specificity"],
               "quality": q["quality"]}
        try:
            decompose = await decompose_question(q["query"], cfg, max_queries=preset["max_queries"])
            rec["en_queries"] = decompose["en_queries"]
            if local:
                corpus, audit = await run_target_recall(
                    q["query"], decompose["en_queries"], depth=args.depth, now_year=now_year, llm_cfg=cfg,
                    preflight_dead=set(), search_one=index.as_search_one(), sources=(LOCAL_SOURCE,),
                    backfill_abstracts=no_backfill, rerank_scores=None if args.no_rerank else rerank_scores,
                    listwise_top=0 if args.no_rerank else LISTWISE_TOP)
                ids = [str(c) for c in q["corpusids"]]
                # 与召回去重同一来源的 key（无 DOI/arXiv 时为 titlefp:…），用于判断是否被筛掉
                key_of = {c: index.candidate(by_id[c], 0, 0.0, "relevance").canonical_key for c in ids if c in by_id}
                baseline = [doc["id"] for doc, _ in index.search(q["query"], limit=200)]
                rec["corpus"] = [[p.get("local_id"), p.get("relevance_status"), p.get("recall_pos"),
                                  p.get("rerank_score")] for p in corpus]
                rec["targets"] = match_local(
                    ids, corpus, key_of=key_of, baseline=baseline, indexable=set(by_id),
                    rejected_keys={str(r.get("key")) for r in (audit.get("filter") or {}).get("rejections") or []})
            else:
                from backend.services.deep_research.preflight import dead_source_names, preflight_research_sources

                missing = [c for c in q["corpusids"] if c not in targets]
                if missing:
                    raise LookupError(f"target_metadata_unavailable: {missing}")
                dead = dead_source_names(await preflight_research_sources())
                rec["dead_sources"] = sorted(dead)
                corpus, audit = await run_target_recall(
                    q["query"], decompose["en_queries"], depth=args.depth, now_year=now_year, llm_cfg=cfg,
                    preflight_dead=dead, rerank_scores=None if args.no_rerank else rerank_scores,
                    listwise_top=0 if args.no_rerank else LISTWISE_TOP)
                rec["targets"] = match_targets(
                    [targets[c] for c in q["corpusids"]], corpus,
                    {str(r.get("key")) for r in (audit.get("filter") or {}).get("rejections") or []})
            rec.update({"corpus_size": len(corpus), "stop_reason": audit.get("stop_reason"),
                        "rerank": audit.get("rerank"),
                        "corpus_after_search": audit.get("corpus_after_search"),
                        "attempts": audit.get("attempts")})
        except Exception as exc:  # noqa: BLE001 —— 单题失败记录在案，不中断
            rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        rec["wall_s"] = round(time.monotonic() - t0, 1)
        if watch.hits and retries.get(q["query"], 0) < QuotaWatch.MAX_RETRIES:
            # 运行中撞到模型限流/额度上限：筛选与规划会按失败降级，结果不可信——
            # 丢弃本题，等额度重置后重跑同一题
            retries[q["query"]] = retries.get(q["query"], 0) + 1
            print(f"    ⚠ 模型限流（{watch.sample[:120]}），丢弃本题结果", flush=True)
            await watch.wait_for_reset()
            i -= 1
            continue
        if watch.hits:
            rec["error"] = f"rate_limited: {watch.sample}"[:300]
        records.append(rec)
        rows = rec.get("targets") or []
        print(f"    → 语料 {rec.get('corpus_size')} 篇，目标命中 {sum(t['found'] for t in rows)}/{len(q['corpusids'])}"
              f"（名次 {[t.get('rank') for t in rows]}，BM25 基线名次 {[t.get('baseline_rank') for t in rows]}）"
              f"{'，' + rec['error'] if rec.get('error') else ''}", flush=True)
        out_path.write_text(json.dumps({"partial": True, "records": records}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return {"corpus": args.corpus, "depth": args.depth, "specificity": args.specificity, "seed": args.seed,
            "subset": args.subset or None,
            "model": cfg.get("model"), "summary": summarize_litsearch(records),
            "by_specificity": {str(v): summarize_litsearch([r for r in records if r.get("specificity") == v])
                               for v in (0, 1) if any(r.get("specificity") == v for r in records)},
            "records": records,
            "_path": str(out_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="bench", required=True)
    s = sub.add_parser("synergy")
    s.add_argument("--dataset", required=True)
    s.add_argument("--negatives", type=int, default=None, help="负例抽样数（默认全量）")
    s.add_argument("--final", action="store_true", help="强制用最终纳入标签作金标准")
    s.add_argument("--question", default="", help="覆盖研究问题（默认用综述题目）")
    li = sub.add_parser("litsearch")
    li.add_argument("--n", type=int, default=30)
    li.add_argument("--specificity", default="0", choices=["0", "1", "all"],
                    help="0=宽泛题 155，1=具体题 442，all=全部 597")
    li.add_argument("--resume", default="", help="续跑已有结果文件（跳过已完成的题）")
    li.add_argument("--subset", default="", help="固定子集文件，如 backend/eval/litsearch_subset_v1.json")
    li.add_argument("--quality", type=int, default=None)
    li.add_argument("--depth", default="quick")
    li.add_argument("--no-rerank", action="store_true", help="关闭终裁重排（对照用）")
    li.add_argument("--corpus", default="local", choices=["local", "online"],
                    help="local=数据集自带语料（不联网检索）；online=真实数据源")
    for p in (s, li):
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--thinking-effort", default="off")
        p.add_argument("--model", default="", help="覆盖系统模型（默认用设置里的模型）")
    args = parser.parse_args()
    result = asyncio.run(run_synergy(args) if args.bench == "synergy" else run_litsearch(args))
    path = Path(result.pop("_path", "")) if result.get("_path") else (
        OUT_DIR / f"public_{args.bench}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result.get("scores") or result.get("summary"), ensure_ascii=False, indent=1))
    print(f"完成 → {path}")


if __name__ == "__main__":
    main()
