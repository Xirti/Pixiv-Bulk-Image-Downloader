from __future__ import annotations

import html
import re
import urllib.parse
from pathlib import Path
from typing import Any


class PixivPolicyError(ValueError):
    pass


def resolve_web_path(root: Path, request_path: str) -> Path:
    decoded = urllib.parse.unquote(urllib.parse.urlsplit(request_path).path)
    relative = decoded.lstrip("/") or "index.html"
    if any(part in {"..", "."} for part in Path(relative).parts) or Path(relative).is_absolute():
        raise PixivPolicyError("invalid static path")
    root_resolved = root.resolve(); candidate = (root_resolved / relative).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise PixivPolicyError("static path escapes web root")
    return candidate


def validate_public_policy(raw: dict[str, Any], detail: bool = False) -> None:
    required = ["xRestrict", "isUnlisted"] + (["isLoginOnly"] if detail else [])
    if any(field not in raw for field in required):
        raise PixivPolicyError("missing public policy field")
    if int(raw["xRestrict"]) != 0 or bool(raw["isUnlisted"]):
        raise PixivPolicyError("restricted artwork")
    if detail and (bool(raw.get("isMasked", False)) or int(raw.get("visibilityScope", 0)) != 0 or bool(raw["isLoginOnly"])):
        raise PixivPolicyError("non-public artwork")


def validate_detail_policy(raw: dict[str, Any], allow_r18: bool = False) -> str:
    required = ["xRestrict", "isUnlisted", "isLoginOnly"]
    if any(field not in raw for field in required): raise PixivPolicyError("missing public policy field")
    restriction = int(raw["xRestrict"])
    if restriction not in ({0, 1} if allow_r18 else {0}) or bool(raw["isUnlisted"]): raise PixivPolicyError("restricted artwork")
    if bool(raw.get("isMasked", False)) or int(raw.get("visibilityScope", 0)) != 0 or bool(raw["isLoginOnly"]): raise PixivPolicyError("non-public artwork")
    return "r18" if restriction == 1 else "safe"


def is_allowed_pixiv_url(url: str, image_only: bool = False) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower()
    if host == "i.pximg.net":
        return bool(image_only)
    return host == "www.pixiv.net"


def _plain_text(value: Any) -> str:
    text = re.sub(r"<br\s*/?>", " ", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def normalize_search_item(raw: dict[str, Any], allow_r18: bool = False) -> dict[str, Any]:
    restriction = int(raw.get("xRestrict", -1))
    if restriction == 0:
        validate_public_policy(raw, detail=False)
    elif restriction == 1 and allow_r18:
        if bool(raw.get("isUnlisted")):
            raise PixivPolicyError("restricted artwork")
    else:
        raise PixivPolicyError("restricted artwork")
    if raw.get("isMasked"):
        raise PixivPolicyError("masked artwork is not available")
    if "visibilityScope" in raw and int(raw["visibilityScope"]) != 0:
        raise PixivPolicyError("non-public artwork is not available")
    artwork_id = str(raw.get("id") or "")
    if not artwork_id.isdigit():
        raise PixivPolicyError("invalid artwork id")
    source_thumb = str(raw.get("url") or "")
    if not is_allowed_pixiv_url(source_thumb, image_only=True):
        raise PixivPolicyError("invalid image host")
    proxy = "/api/pixiv/image?" + urllib.parse.urlencode({"url": source_thumb})
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    return {
        "id": artwork_id,
        "restriction": "r18" if restriction == 1 else "safe",
        "source": "pixiv",
        "title": str(raw.get("title") or "未命名作品"),
        "artist": str(raw.get("userName") or "未知画师"),
        "userId": str(raw.get("userId") or ""),
        "tags": [str(tag) for tag in tags[:30]],
        "pages": max(1, int(raw.get("pageCount") or 1)),
        "width": max(0, int(raw.get("width") or 0)),
        "height": max(0, int(raw.get("height") or 0)),
        "bookmarks": max(0, int(raw.get("bookmarkCount") or 0)),
        "date": str(raw.get("createDate") or "")[:10],
        "description": _plain_text(raw.get("description")),
        "workType": {0: "illustration", 1: "manga", 2: "ugoira"}.get(int(raw.get("illustType", 0)), "illustration"),
        "aiGenerated": int(raw.get("aiType") or 1) == 2,
        "thumb": proxy,
        "qualities": [
            {"id": "original", "label": "Pixiv 原图", "width": int(raw.get("width") or 0), "height": int(raw.get("height") or 0)},
            {"id": "regular", "label": "Pixiv 常规预览", "width": 0, "height": 0},
        ],
        "formats": [{"id": "source", "label": "保留源格式（推荐）"}],
    }


def build_search_url(tag: str, page: int, mode: str = "safe", start_date=None, end_date=None) -> str:
    if mode not in {"safe", "r18"}:
        raise PixivPolicyError("unsupported search mode")
    clean_tag = str(tag).strip()[:60] or "原创"
    encoded = urllib.parse.quote(clean_tag, safe="")
    params = {"word": clean_tag, "order": "date_d", "mode": mode, "p": max(1, int(page)), "s_mode": "s_tag_full", "type": "all", "lang": "zh"}
    if start_date and end_date:
        params["scd"] = start_date.isoformat(); params["ecd"] = end_date.isoformat()
    query = urllib.parse.urlencode(params)
    return f"https://www.pixiv.net/ajax/search/artworks/{encoded}?{query}"


def _validated_user_id(user_id: str) -> str:
    clean = str(user_id or "").strip()
    if not clean.isascii() or not clean.isdigit():
        raise PixivPolicyError("invalid Pixiv user id")
    return clean


def build_user_search_url(author: str) -> str:
    clean = str(author or "").strip()[:60]
    if not clean:
        raise PixivPolicyError("empty Pixiv author name")
    encoded = urllib.parse.quote(clean, safe="")
    query = urllib.parse.urlencode({"word": clean, "s_mode": "s_usr", "type": "user", "lang": "zh"})
    return f"https://www.pixiv.net/ajax/search/users/{encoded}?{query}"


def build_user_profile_all_url(user_id: str) -> str:
    return f"https://www.pixiv.net/ajax/user/{_validated_user_id(user_id)}/profile/all?lang=zh"


def build_user_profile_works_url(user_id: str, artwork_ids) -> str:
    clean_user_id = _validated_user_id(user_id)
    ids: list[str] = []
    seen: set[str] = set()
    for artwork_id in artwork_ids:
        clean = str(artwork_id or "").strip()
        if not clean.isascii() or not clean.isdigit() or clean in seen:
            continue
        seen.add(clean)
        ids.append(clean)
        if len(ids) >= 48:
            break
    if not ids:
        raise PixivPolicyError("empty Pixiv artwork id list")
    params = [("ids[]", artwork_id) for artwork_id in ids]
    params.extend((("work_category", "illustManga"), ("is_first_page", "0"), ("lang", "zh")))
    return f"https://www.pixiv.net/ajax/user/{clean_user_id}/profile/illusts?{urllib.parse.urlencode(params)}"


def _proxy_image(url: str) -> str:
    if not is_allowed_pixiv_url(url, image_only=True):
        raise PixivPolicyError("invalid image host")
    return "/api/pixiv/image?" + urllib.parse.urlencode({"url": url})


def normalize_detail(raw: dict[str, Any], pages: list[dict[str, Any]], allow_r18: bool = False) -> dict[str, Any]:
    restriction = validate_detail_policy(raw, allow_r18=allow_r18)
    artwork_id = str(raw.get("illustId") or raw.get("id") or "")
    if not artwork_id.isdigit():
        raise PixivPolicyError("invalid artwork id")
    tag_rows = (raw.get("tags") or {}).get("tags") if isinstance(raw.get("tags"), dict) else []
    page_images = []
    for page in pages:
        urls = page.get("urls") or {}
        page_images.append({"width": int(page.get("width") or 0), "height": int(page.get("height") or 0), "regular": _proxy_image(str(urls.get("regular") or "")), "original": _proxy_image(str(urls.get("original") or ""))})
    return {"id": artwork_id, "restriction": restriction, "source": "pixiv", "title": str(raw.get("title") or raw.get("illustTitle") or "未命名作品"), "artist": str(raw.get("userName") or "未知画师"), "userId": str(raw.get("userId") or ""), "tags": [str(row.get("tag")) for row in (tag_rows or []) if row.get("tag")][:30], "pages": len(page_images), "width": int(raw.get("width") or 0), "height": int(raw.get("height") or 0), "bookmarks": int(raw.get("bookmarkCount") or 0), "date": str(raw.get("createDate") or "")[:10], "description": _plain_text(raw.get("description") or raw.get("illustComment")), "thumb": page_images[0]["regular"] if page_images else "", "pageImages": page_images, "qualities": [{"id":"original","label":"Pixiv 原图","width":int(raw.get("width") or 0),"height":int(raw.get("height") or 0)},{"id":"regular","label":"Pixiv 常规预览","width":0,"height":0}], "formats":[{"id":"source","label":"保留源格式（推荐）"}]}


def should_retry_status(status: int) -> bool:
    return 500 <= int(status) <= 599


def safe_artwork_stem(title: str, artwork_id: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(title)).strip(' ._')
    clean = re.sub(r'_+', '_', clean)[:100]
    return f"{clean}_{artwork_id}" if clean else f"pixiv_{artwork_id}"


def resolve_download_target(root: Path, title: str, artwork_id: str, create_folder: bool) -> Path:
    root = Path(root)
    return root / safe_artwork_stem(title, artwork_id) if create_folder else root


def safe_download_name(artwork_id: str, page: int, extension: str) -> str:
    if not str(artwork_id).isdigit() or not isinstance(page, int) or page < 0:
        raise ValueError("invalid artwork id or page")
    ext = str(extension).lower().lstrip(".")
    if ext not in {"jpg", "jpeg", "png", "gif", "webp"}:
        raise ValueError("unsupported image extension")
    return f"{artwork_id}_p{page}.{ext}"
