"""same-dataset：在相同数据集上做实验（experiments.datasets 集合重叠）。"""

from .base import SetFacet
from ..features import PaperFeatures


class DatasetFacet(SetFacet):
    name = "dataset"
    label = "数据集"
    default_weight = 0.05

    def items(self, pf: PaperFeatures) -> list[str]:
        return pf.datasets
