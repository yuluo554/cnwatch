"""The one disclaimer sentence, shared by every surface that must show it.

plan/07 requires the same wording in three places: the README, the CLI help and
the daily report footer. Keeping it in one module is what makes "identical"
checkable — three hand-copied strings drift silently, and a typo in one of them
still reads like a disclaimer to a human reviewer.

The sentence is deliberately narrow. It says the tool aggregates public
information; it does not rate issuers, forecast returns or imply a direction.
"""

DISCLAIMER = "本工具仅聚合公开信息，不构成投资建议。"

DATA_SOURCE = "巨潮资讯网（CNINFO）公开公告列表"

__all__ = ["DATA_SOURCE", "DISCLAIMER"]
