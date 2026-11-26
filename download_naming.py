"""Validate download naming rules and render safe Windows filenames."""

import re
from typing import TypedDict

from pixiv_adapter import safe_artwork_stem, valid_pixiv_id


class NamingRule(TypedDict):
    fields: list[str]
    separator: str
    dateFormat: str
    pageWidth: int


BUILTIN_NAMING_IDS = ("legacy", "pid-title", "pid-page", "title-pid", "author", "date")
DEFAULT_DOWNLOAD_NAMING = {
    "defaultId": "legacy",
    "visible": list(BUILTIN_NAMING_IDS),
    "custom": [],
}
NAMING_FIELDS = ("author", "uid", "pid", "date", "title", "page")
NAMING_SEPARATORS = ("_", "-", " ", "")
NAMING_DATE_FORMATS = ("YYYY-MM-DD", "YYYYMMDD", "YYYY/MM/DD")
NAMING_PAGE_WIDTHS = (2, 3, 4)
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\ud800-\udfff]+')
_RESERVED_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", re.IGNORECASE)


def validate_naming_rule(raw: object) -> NamingRule:
    error = "文件命名规则无效"
    if not isinstance(raw, dict) or raw.keys() != {"fields", "separator", "dateFormat", "pageWidth"}:
        raise ValueError(error)
    fields = raw["fields"]
    if (not isinstance(fields, list) or not 1 <= len(fields) <= len(NAMING_FIELDS)
            or any(field not in NAMING_FIELDS for field in fields)
            or len(set(fields)) != len(fields)
            or raw["separator"] not in NAMING_SEPARATORS
            or raw["dateFormat"] not in NAMING_DATE_FORMATS
            or type(raw["pageWidth"]) is not int or raw["pageWidth"] not in NAMING_PAGE_WIDTHS):
        raise ValueError(error)
    return raw


def validate_download_naming(raw: object) -> None:
    error = "文件命名预设无效，请核对默认项和显示列表"
    if not isinstance(raw, dict) or raw.keys() != {"defaultId", "visible", "custom"}:
        raise ValueError(error)
    custom, visible = raw["custom"], raw["visible"]
    if not isinstance(custom, list) or len(custom) > 12:
        raise ValueError(error)
    ids = set(BUILTIN_NAMING_IDS)
    for preset in custom:
        if not isinstance(preset, dict) or preset.keys() != {"id", "label", "rule"}:
            raise ValueError(error)
        key, label = preset["id"], preset["label"]
        if (not isinstance(key, str) or not re.fullmatch(r"custom-[0-9a-f]{32}", key) or key in ids
                or not isinstance(label, str) or not 1 <= len(label) <= 32
                or label != label.strip() or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in label)):
            raise ValueError(error)
        validate_naming_rule(preset["rule"])
        ids.add(key)
    if (not isinstance(visible, list) or not 1 <= len(visible) <= len(ids)
            or any(not isinstance(key, str) or key not in ids for key in visible)
            or len(set(visible)) != len(visible) or raw["defaultId"] not in visible):
        raise ValueError(error)


def _clean_component(value: str, limit: int, fallback: str) -> str:
    clean = re.sub(r"_+", "_", _INVALID_FILENAME_CHARS.sub("_", value)).strip(" ._")
    clipped = clean.encode("utf-16-le")[:limit * 2].decode("utf-16-le", errors="ignore").rstrip(" ._")
    return clipped or fallback


def artwork_file_stem(item: dict, page: int | None, rule: NamingRule | None) -> str:
    pid = str(item["id"])
    title = str(item.get("title") or "")
    if rule is None:
        suffix = "ugoira" if page is None else f"p{page}"
        return f"{safe_artwork_stem(title, pid)}_{suffix}"

    separator = rule["separator"]
    date = item.get("date") or "日期未知"
    if rule["dateFormat"] == "YYYYMMDD":
        date = date.replace("-", "")
    elif rule["dateFormat"] == "YYYY/MM/DD":
        date = date.replace("-", "／")
    page_count = item["pages"]
    width = max(rule["pageWidth"], len(str(page_count)))
    page_label = "ugoira" if page is None else f"p{page + 1:0{width}d}"
    uid = str(item.get("userId") or "")
    values = {
        "author": _clean_component(str(item.get("artist") or ""), 50, "未知画师"),
        "uid": uid if valid_pixiv_id(uid) else "未知UID",
        "pid": pid,
        "date": date,
        "title": _clean_component(title, 90, "未命名作品"),
        "page": page_label,
    }
    fields = rule["fields"]
    parts = [values[field] for field in fields]
    if "page" not in fields and (page is None or page_count > 1):
        parts.append(page_label)
    stem = separator.join(parts)
    if _RESERVED_NAME.fullmatch(stem.partition(".")[0].rstrip(" ")):
        stem = "_" + stem
    return stem
