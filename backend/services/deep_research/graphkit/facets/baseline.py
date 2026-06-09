"""same-baseline：对比了相同的基线方法（experiments.baselines 集合重叠）。"""

from .base import SetFacet
from ..features import PaperFeatures


class BaselineFacet(SetFacet):
    name = "baseline"
    label = "baseline"
    default_weight = 0.15

    def items(self, pf: PaperFeatures) -> list[str]:
        return pf.baselines
