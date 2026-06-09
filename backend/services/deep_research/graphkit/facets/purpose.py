"""same-purpose：研究目的相似（problem + one_sentence + key_contributions）。

这是最关键的关系——它纯靠语义，**不依赖引用**，因此能连出"目标一致但彼此不互引"
的论文（正是帮学者发现多种同方向思路的核心）。
"""

from .base import EmbeddingFacet
from ..features import PaperFeatures


class PurposeFacet(EmbeddingFacet):
    name = "purpose"
    label = "研究目的"
    default_weight = 0.5

    def text(self, pf: PaperFeatures) -> str:
        return pf.purpose_text
