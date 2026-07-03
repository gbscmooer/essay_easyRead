"""Cross-Encoder 相似度 provider。"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlmodel import Session

from ...database import get_engine
from ...ai_config import get_reranker_config
from ...paper_profile import load_paper_profiles
from ..reranker import rerank

logger = logging.getLogger(__name__)


class CrossEncoderProvider:
    name = "cross_encoder"

    def __init__(self, candidate_k: int = 12, blend: float = 0.7, min_base_score: float = 0.08, **_):
        self.candidate_k = max(1, int(candidate_k))
        self.blend = min(1.0, max(0.0, float(blend)))
        self.min_base_score = min(1.0, max(0.0, float(min_base_score)))

    async def score_pairs(self, papers: list, *, ws_id: int) -> list[tuple[int, int, float]]:
        if len(papers) < 2:
            return []

        from .bge_centroid import BgeCentroidProvider

        base_pairs = await BgeCentroidProvider(center=True).score_pairs(papers, ws_id=ws_id)
        if not base_pairs:
            return []

        base_map = {(min(a, b), max(a, b)): score for a, b, score in base_pairs}
        cfg = get_reranker_config()
        if not cfg.get("enabled", True) or not cfg.get("api_key") or not cfg.get("base_url"):
            logger.info("cross_encoder: reranker not configured, fallback to bge_centroid")
            return base_pairs

        engine = get_engine()
        with Session(engine) as session:
            profiles = load_paper_profiles(session, [p.id for p in papers])

        query_texts = {
            pid: profile.canonical_text() or profile.title
            for pid, profile in profiles.items()
        }

        neighbors: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for a, b, score in base_pairs:
            if score < self.min_base_score:
                continue
            neighbors[a].append((b, score))
            neighbors[b].append((a, score))

        for pid in list(neighbors.keys()):
            neighbors[pid].sort(key=lambda item: item[1], reverse=True)
            neighbors[pid] = neighbors[pid][: self.candidate_k]

        rerank_scores: dict[tuple[int, int], list[float]] = defaultdict(list)
        for pid, items in neighbors.items():
            query = query_texts.get(pid, "")
            if not query.strip() or not items:
                continue
            doc_ids = [nb for nb, _ in items]
            docs = [query_texts.get(nb, "") for nb in doc_ids]
            if not any(doc.strip() for doc in docs):
                continue
            try:
                ranked = await rerank(query, docs, top_n=len(docs))
            except Exception as exc:  # pragma: no cover - rerank 自身已做回退，这里再兜底一层
                logger.warning(f"cross_encoder: rerank failed for paper {pid}: {exc}")
                continue
            for item in ranked:
                idx = item.get("index")
                if idx is None or idx < 0 or idx >= len(doc_ids):
                    continue
                nb = doc_ids[idx]
                key = (min(pid, nb), max(pid, nb))
                rerank_scores[key].append(float(item.get("score") or 0.0))

        out: list[tuple[int, int, float]] = []
        for (a, b), base_score in base_map.items():
            rr = rerank_scores.get((a, b), [])
            if rr and max(rr) > 0:
                rerank_score = sum(rr) / len(rr)
                score = self.blend * rerank_score + (1.0 - self.blend) * base_score
            else:
                score = base_score
            out.append((a, b, round(min(1.0, max(0.0, score)), 4)))

        return out
