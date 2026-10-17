"""Title normalization: the single pre-match transform.

The engine and the rule-package generator must agree byte for byte, or a title
that the generator calibrated would classify differently at runtime. The test
suite therefore compares this function against ``tools/gen_rules.normalize``
over every frozen title rather than trusting the two copies to stay in step.

The transform is exactly what the rule package declares:

* full-width digits, Latin letters and parentheses become half-width;
* ``<em>`` / ``</em>`` are removed (the upstream ``searchkey`` highlight);
* whitespace is removed outright, not collapsed — a title is matched as a
  contiguous string, so ``半 年 度 报 告`` must still hit ``半年度报告``.
"""

from __future__ import annotations

_FULL_WIDTH = "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）"
_HALF_WIDTH = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz()"

_TRANSLATION = {ord(full): half for full, half in zip(_FULL_WIDTH, _HALF_WIDTH)}
# The ideographic space is whitespace like any other; dropping it below is what
# makes this entry matter, but keeping it explicit documents the intent.
_TRANSLATION[0x3000] = " "


def normalize_title(title: str) -> str:
    """Normalize a title for pattern matching. Case is preserved."""

    if not title:
        return ""
    text = str(title).translate(_TRANSLATION)
    text = text.replace("<em>", "").replace("</em>", "")
    return "".join(text.split())


def is_usable_title(title: str, limit: int) -> bool:
    """A title the engine can classify at all.

    Empty after normalization or longer than ``limit`` means the engine would
    be guessing; the caller records it as ``unclassified`` instead.
    """

    if not isinstance(title, str) or not title.strip():
        return False
    return len(title) <= limit
