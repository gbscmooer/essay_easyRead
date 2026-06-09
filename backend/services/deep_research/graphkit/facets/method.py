"""same-method：方法/思路相似（method.overview + pipeline）。"""

from .base import EmbeddingFacet
from ..features import PaperFeatures


class MethodFacet(EmbeddingFacet):
    name = "method"
    label = "方法"
    default_weight = 0.2

    def text(self, pf: PaperFeatures) -> str:
        return pf.method_text
