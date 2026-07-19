"""Reviewed, local-only aliases for common anime / game fandom tags.

This file contains no user configuration or account data.  Aliases are deliberately
narrow: they describe the same character, title, or franchise spelling rather than
broadly related concepts.  Search still matches Pixiv tag fields only.
"""

from __future__ import annotations

import unicodedata


ANIME_TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "miku": ("miku", "初音未来", "初音ミク", "hatsune miku"),
    "初音未来": ("miku", "初音未来", "初音ミク", "hatsune miku"),
    "初音ミク": ("miku", "初音未来", "初音ミク", "hatsune miku"),
    "hatsune miku": ("miku", "初音未来", "初音ミク", "hatsune miku"),
    "saber": ("saber", "セイバー", "阿尔托莉雅", "artoria pendragon"),
    "セイバー": ("saber", "セイバー", "阿尔托莉雅", "artoria pendragon"),
    "阿尔托莉雅": ("saber", "セイバー", "阿尔托莉雅", "artoria pendragon"),
    "artoria pendragon": ("saber", "セイバー", "阿尔托莉雅", "artoria pendragon"),
    "rem": ("rem", "蕾姆", "レム"),
    "蕾姆": ("rem", "蕾姆", "レム"),
    "レム": ("rem", "蕾姆", "レム"),
    "asuna": ("asuna", "亚丝娜", "結城明日奈", "结城明日奈"),
    "亚丝娜": ("asuna", "亚丝娜", "結城明日奈", "结城明日奈"),
    "結城明日奈": ("asuna", "亚丝娜", "結城明日奈", "结城明日奈"),
    "结城明日奈": ("asuna", "亚丝娜", "結城明日奈", "结城明日奈"),
    "genshin": ("genshin", "原神", "原神イラスト"),
    "原神": ("原神",),
    "honkai star rail": ("honkai star rail", "崩坏：星穹铁道", "崩坏星穹铁道", "崩壊：スターレイル"),
    "崩坏：星穹铁道": ("honkai star rail", "崩坏：星穹铁道", "崩坏星穹铁道", "崩壊：スターレイル"),
    "崩坏星穹铁道": ("honkai star rail", "崩坏：星穹铁道", "崩坏星穹铁道", "崩壊：スターレイル"),
    "blue archive": ("blue archive", "碧蓝档案", "ブルーアーカイブ"),
    "碧蓝档案": ("blue archive", "碧蓝档案", "ブルーアーカイブ"),
    "ブルーアーカイブ": ("blue archive", "碧蓝档案", "ブルーアーカイブ"),
}


def normalize_alias_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def aliases_for(value: str) -> tuple[str, ...]:
    clean = str(value or "").strip()
    aliases = ANIME_TAG_ALIASES.get(normalize_alias_key(clean))
    if not aliases:
        return (clean,)
    # Preserve dictionary order while removing accidental duplicate spellings.
    result: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        key = normalize_alias_key(alias)
        if key and key not in seen:
            seen.add(key)
            result.append(alias)
    return tuple(result or (clean,))
