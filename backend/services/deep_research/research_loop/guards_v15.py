"""A positive passage or an omission cannot prove an exclusive/negative claim."""

import re

NEGATIVE = re.compile(
    r"(?:未|没有|不曾).{0,16}(?:说明|提到|展开|给出|提供|研究|报告|实现)|而非|从不|never|does not.{0,18}(?:describe|provide|report|study)",
    re.I,
)
SOURCE_NEGATIVE = re.compile(
    r"\b(?:not|no|without|never)\b|rather than|instead of|未|没有|而非|不", re.I
)


def explicit_negative(text, checked_explicit, quotes):
    return not NEGATIVE.search(text) or (
        checked_explicit is True
        and any(SOURCE_NEGATIVE.search(quote) for quote in quotes)
    )
