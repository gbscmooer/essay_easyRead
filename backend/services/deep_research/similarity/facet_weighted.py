"""
Facet-Weighted 相似度：可解释的多维度论文相似度

把"两篇论文有多像"拆成 4 个可解释维度，各自打分后加权求和：
- purpose（研究目的）：problem + one_sentence + key_contributions 的语义相似度
- method（方法）：     method.overview + pipeline 的语义相似度
- baseline：           experiments.baselines 列表的集合重叠（Jaccard）
- dataset（数据集）：   experiments.datasets 列表的集合重叠（Jaccard）

最终分 = Σ(维度分 × 权重)。权重在 ai_config.similarity.facet_weighted.weights 配置，
本类会自动归一化到 1。每对论文的分解结果存在 self.details 里，graph_builder 会把它
挂到 edge.detail 上——前端点击相似边即可看到"为什么相似、各维度占多少"。

数据来源是论文解读结果（AnalysisResult）。因此：
- 两篇都已解读 → 完整四维分解
- 有一篇没解读（如联网扩展的叶子论文）→ 无 facet 数据，该对不计相似边（优雅降级）

purpose/method 的语义分默认用 embedding 余弦（中心化，理由见 bge_centroid）；
也可关 use_embedding 走纯词重叠（lexical），零模型依赖、确定性，便于离线/测试。
"""

import logging
import numpy as np
from sqlmodel import Session, create_engine, select

from ....config import settings
from ....models.analysis import AnalysisResult
from ..embedder import embed_texts

logger = logging.getLogger(__name__)

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.resolved_database_url, connect_args={"check_same_thread": False})
    return _engine


DEFAULT_WEIGHTS = {"purpose": 0.5, "method": 0.2, "baseline": 0.15, "dataset": 0.05}
FACET_CN = {"purpose": "研究目的", "method": "方法", "baseline": "baseline", "dataset": "数据集"}

_STOP = set("的 了 在 是 和 与 或 对 中 为 上 下 一个 一种 我们 本文 提出 方法 the a an of to in for on with and or is are this that we our by".split())


def _norm_set(items) -> set[str]:
    return {str(x).strip().lower() for x in (items or []) if str(x).strip()}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _keywords(text: str) -> set[str]:
    out: set[str] = set()
    for w in str(text or "").lower().replace("，", " ").replace("。", " ").split():
        w = w.strip(".,;:!?()[]{}\"'`/<>")
        if len(w) > 1 and w not in _STOP:
            out.add(w)
    return out


def _lexical(a: str, b: str) -> float:
    return _jaccard(_keywords(a), _keywords(b))


def _level_note(label: str, sim: float) -> str:
    if sim >= 0.6:
        return f"{label}高度一致"
    if sim >= 0.3:
        return f"{label}部分相关"
    return f"{label}差异较大"


class FacetWeightedProvider:
    name = "facet_weighted"

    def __init__(self, weights: dict | None = None, center: bool = True, use_embedding: bool = True, **_):
        w = {**DEFAULT_WEIGHTS, **(weights or {})}
        total = sum(max(0.0, float(v)) for v in w.values()) or 1.0
        self.weights = {k: max(0.0, float(v)) / total for k, v in w.items()}  # 归一化到 1
        self.center = center
        self.use_embedding = use_embedding
        self.details: dict[tuple[int, int], dict] = {}

    def _load_facets(self, papers: list) -> dict[int, dict]:
        """从 AnalysisResult 读取每篇论文的 facet 文本/列表；如果无解读结果，回退使用 graph_digest 速读结果。"""
        facets: dict[int, dict] = {}
        pids = [p.id for p in papers]
        with Session(_get_engine()) as s:
            analyses = {a.paper_id: a for a in s.exec(select(AnalysisResult).where(AnalysisResult.paper_id.in_(pids))).all()}
            
        for p in papers:
            a = analyses.get(p.id)
            digest = getattr(p, "graph_digest", None)
            
            if a is not None:
                method = a.method or {}
                exp = a.experiments or {}
                purpose_text = " ".join(filter(None, [a.problem, a.one_sentence, *(a.key_contributions or [])]))
                method_text = " ".join(filter(None, [method.get("overview", ""), method.get("pipeline", "")]))
                datasets = [d for d in (exp.get("datasets") or []) if str(d).strip()]
                baselines = [b for b in (exp.get("baselines") or []) if str(b).strip()]
            elif digest:
                purpose_text = " ".join(filter(None, [
                    digest.get("problem", ""), digest.get("one_sentence", ""),
                    *(digest.get("key_contributions") or []),
                ]))
                method_text = digest.get("method", "") or ""
                datasets = [d for d in (digest.get("datasets") or []) if str(d).strip()]
                baselines = [b for b in (digest.get("baselines") or []) if str(b).strip()]
            else:
                continue
                
            facets[p.id] = {
                "purpose": purpose_text,
                "method": method_text,
                "datasets": datasets,
                "baselines": baselines,
                "datasets_norm": _norm_set(datasets),
                "baselines_norm": _norm_set(baselines),
            }
        return facets

    async def _facet_vectors(self, ids: list[int], key: str, facets: dict) -> dict[int, np.ndarray] | None:
        """对某个 facet 文本批量 embedding，中心化+归一化；失败返回 None（调用方回退 lexical）。"""
        texts = [facets[i][key] or "" for i in ids]
        try:
            vecs = await embed_texts(texts)
        except Exception as ex:
            logger.warning(f"facet_weighted: embed '{key}' failed, fallback to lexical: {ex}")
            return None
        mat = np.asarray(vecs, dtype=float)
        if mat.ndim < 2 or mat.shape[0] != len(ids):
            return None
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        if self.center and len(ids) >= 3:
            mat = mat - mat.mean(axis=0)
            mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        return {i: mat[k] for k, i in enumerate(ids)}

    def _shared(self, fa: dict, fb: dict, field: str) -> list[str]:
        """两篇论文在 datasets/baselines 上的共享项（保留原始大小写，取 a 的写法）。"""
        b_norm = fb[f"{field}_norm"]
        return [orig for orig in fa[field] if str(orig).strip().lower() in b_norm]

    async def score_pairs(self, papers: list, *, ws_id: int) -> list[tuple[int, int, float]]:
        self.details = {}
        pids = [p.id for p in papers]
        facets = self._load_facets(papers)
        usable = [pid for pid in pids if pid in facets]
        if len(usable) < 2:
            logger.warning(f"facet_weighted: 只有 {len(usable)} 篇论文有解读结果，无法计算 facet 相似度")
            return []

        pv = await self._facet_vectors(usable, "purpose", facets) if self.use_embedding else None
        mv = await self._facet_vectors(usable, "method", facets) if self.use_embedding else None

        out: list[tuple[int, int, float]] = []
        for i in range(len(usable)):
            for j in range(i + 1, len(usable)):
                a, b = usable[i], usable[j]
                fa, fb = facets[a], facets[b]

                p_sim = max(0.0, float(np.dot(pv[a], pv[b]))) if pv is not None else _lexical(fa["purpose"], fb["purpose"])
                m_sim = max(0.0, float(np.dot(mv[a], mv[b]))) if mv is not None else _lexical(fa["method"], fb["method"])
                d_shared = self._shared(fa, fb, "datasets")
                b_shared = self._shared(fa, fb, "baselines")
                d_sim = _jaccard(fa["datasets_norm"], fb["datasets_norm"])
                bl_sim = _jaccard(fa["baselines_norm"], fb["baselines_norm"])

                raw = {"purpose": p_sim, "method": m_sim, "baseline": bl_sim, "dataset": d_sim}
                shared = {"baseline": b_shared, "dataset": d_shared}
                reason = {
                    "purpose": _level_note("研究目的", p_sim),
                    "method": _level_note("方法", m_sim),
                    "baseline": ("共享: " + ", ".join(b_shared)) if b_shared else "无共享 baseline",
                    "dataset": ("共享: " + ", ".join(d_shared)) if d_shared else "无共享数据集",
                }

                components = []
                score = 0.0
                for facet in ("purpose", "method", "baseline", "dataset"):
                    w = self.weights.get(facet, 0.0)
                    sim = round(raw[facet], 4)
                    contrib = round(sim * w, 4)
                    score += contrib
                    components.append({
                        "facet": facet,
                        "label": FACET_CN[facet],
                        "sim": sim,
                        "weight": round(w, 4),
                        "contribution": contrib,
                        "reason": reason[facet],
                        "shared": shared.get(facet, []),
                    })

                score = round(min(score, 1.0), 4)
                out.append((a, b, score))
                self.details[(min(a, b), max(a, b))] = {"weight": score, "components": components}

        return out
