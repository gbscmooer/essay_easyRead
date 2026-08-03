"""
lexical — IDF 加权术语重叠（纯函数，无 DB）

为什么图谱需要一路词法信号
--------------------------------------------------
稠密向量把**低频专名压掉**：一篇讲 "few-shot anomaly detection on MVTec-AD with
DINOv2" 的论文和一篇讲 "industrial defect segmentation" 的论文，在句向量空间里几乎
一样近（都是"工业视觉 + 视觉骨干 + 检测"），因为区分二者的信息全在几个稀有词上，
而稀有词在均值池化的句向量里权重极低。细分方向恰恰是靠这些词分开的：
任务名（open-vocabulary / few-shot）、数据集名（MVTec-AD / COCO）、骨干名（DINOv2）。

本模块用 IDF 加权的二值向量余弦度量这种重叠：

    score(A,B) = Σ_{t∈A∩B} idf(t)² / (‖A‖ · ‖B‖),  ‖X‖ = sqrt(Σ_{t∈X} idf(t)²)

- 工作区里人人都写的词（network / learning / 实验）idf≈0，贡献≈0；
- 只有两三篇论文共有的词（mvtec / dinov2 / open-vocabulary）idf 高，一命中就拉满。

分词覆盖中英混排：英文保留连字符与数字型号（`bge-m3` / `dinov2` / `3d`），中文用
bigram（LLM 速读产出的 problem/one_sentence 常为中文）。
"""

from __future__ import annotations

import math
import re

# 英文词：字母开头，允许内部连字符/数字（dinov2、bge-m3、mvtec-ad、3d 由下面单独兜底）
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]{1,}")
# 纯数字开头的型号词（3d、2d、1080ti）
_ALNUM_RE = re.compile(r"\d[a-zA-Z]+[a-zA-Z0-9]*")
_CJK_RE = re.compile(r"[一-鿿]")

# 学术写作里到处都是、对区分细分方向零信息量的词。IDF 本身也会压低它们，
# 但小工作区（N<10）里 IDF 估计不稳，显式停用更稳妥。
_STOP = frozenset("""
the a an of to in for on with and or is are was were be been this that these those we our us they their it its
as at by from into over under via using use used uses can could may might will would should not no non but if then
than which who whom whose what when where how all any both each few more most other some such only own same so
paper papers study studies work works method methods approach approaches model models framework frameworks system
systems network networks module modules task tasks problem problems result results experiment experiments dataset
datasets data based propose proposed proposes present presents show shows shown novel new state art achieve achieves
performance accuracy training train trained inference learning learn deep neural end
的 了 在 是 和 与 或 对 中 为 上 下 一个 一种 我们 本文 提出 方法 模型 网络 实验 结果 数据 使用 通过 进行 可以 并且 以及
""".split())

_MIN_LEN = 3


def extract_terms(*texts: str) -> set[str]:
    """从若干文本抽取术语集合（小写、去停用词、中文 bigram）。"""
    out: set[str] = set()
    for text in texts:
        s = str(text or "")
        if not s:
            continue
        low = s.lower()
        for m in _WORD_RE.findall(low):
            w = m.strip("-")
            if len(w) >= _MIN_LEN and w not in _STOP:
                out.add(w)
        for m in _ALNUM_RE.findall(low):
            if len(m) >= 2:
                out.add(m)
        cjk = _CJK_RE.findall(s)
        for i in range(len(cjk) - 1):
            bg = cjk[i] + cjk[i + 1]
            if bg not in _STOP:
                out.add(bg)
    return out


def normalize_entity(name: str) -> str:
    """数据集 / baseline 名的规范形式（大小写、空白、常见分隔符差异归一）。"""
    s = re.sub(r"[\s_]+", "-", str(name or "").strip().lower())
    return re.sub(r"[^a-z0-9一-鿿\-+]", "", s).strip("-")


def build_idf(doc_terms: dict[int, set[str]], *, smooth: float = 1.0) -> dict[str, float]:
    """idf(t) = log(1 + N / df(t))。N 为文档数，df 为含该词的文档数。"""
    n_docs = max(1, len(doc_terms))
    df: dict[str, int] = {}
    for terms in doc_terms.values():
        for t in terms:
            df[t] = df.get(t, 0) + 1
    return {t: math.log(smooth + n_docs / d) for t, d in df.items()}


def idf_cosine(
    a: set[str], b: set[str], idf: dict[str, float], *, top_shared: int = 6
) -> tuple[float, list[str]]:
    """IDF 加权二值向量余弦 + 贡献最大的共享词（供前端解释）。"""
    if not a or not b:
        return 0.0, []
    shared = a & b
    if not shared:
        return 0.0, []
    num = sum(idf.get(t, 0.0) ** 2 for t in shared)
    na = math.sqrt(sum(idf.get(t, 0.0) ** 2 for t in a))
    nb = math.sqrt(sum(idf.get(t, 0.0) ** 2 for t in b))
    if na <= 0 or nb <= 0:
        return 0.0, []
    score = num / (na * nb)
    ranked = sorted(shared, key=lambda t: -idf.get(t, 0.0))[:top_shared]
    return min(1.0, score), ranked
