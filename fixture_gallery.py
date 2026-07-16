from __future__ import annotations

ARTISTS = ["momo atelier", "星屑ミルク", "nagi", "うみの窓", "白桃ソーダ", "こはる"]
TITLES = ["云朵邮局", "晚风与猫", "星星收集计划", "海边的约定", "草莓汽水日", "月光散步"]
PALETTES = [("#ffd8e8", "#8fb7ff"), ("#ffe6ad", "#ef9aaf"), ("#cabcf8", "#80cfd0"), ("#bde8dc", "#f7b6cf"), ("#ffd1b8", "#b5cdf9"), ("#e8d0ff", "#ffbfc8")]


def artwork_svg(i: int, page: int = 0, size: str = "preview") -> bytes:
    a, b = PALETTES[(i + page) % len(PALETTES)]
    dimensions = {"original": (2400, 1800), "large": (1800, 1350), "preview": (1200, 900)}
    w, h = dimensions.get(size, dimensions["preview"])
    title = TITLES[i % len(TITLES)]
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 900 680">
    <defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient>
    <filter id="n"><feTurbulence baseFrequency=".65" numOctaves="3" seed="{i+page}"/><feBlend mode="soft-light" in="SourceGraphic"/></filter></defs>
    <rect width="900" height="680" rx="42" fill="url(#g)"/><g opacity=".82" filter="url(#n)">
    <circle cx="{180+page*35}" cy="170" r="100" fill="#fff8"/><path d="M0 530 Q210 390 430 520 T900 470 V680 H0Z" fill="#fff9"/>
    <path d="M560 180 q70-115 140 0 q-20 100-70 130 q-55-30-70-130" fill="#fff7"/>
    <g fill="#fff"><circle cx="595" cy="202" r="8"/><circle cx="670" cy="202" r="8"/></g></g>
    <text x="60" y="605" fill="#342b3a" font-size="34" font-family="Georgia,serif">{title} · {page+1}</text></svg>'''
    return svg.encode("utf-8")


def records(tag: str):
    result = []
    for i in range(24):
        width, height = 2400 + i * 30, 1800 + i * 20
        result.append({
            "id": str(81024000 + i),
            "title": TITLES[i % 6] + (f" · {i // 6 + 1}" if i >= 6 else ""),
            "artist": ARTISTS[i % 6], "userId": str(12000 + i),
            "tags": [tag or "原创", "插画", ["猫", "风景", "少女"][i % 3]],
            "pages": 12 if i % 12 == 1 else (4 if i % 6 in (1, 4) else (2 if i % 6 == 3 else 1)),
            "width": width, "height": height, "bookmarks": 1260 + i * 847,
            "date": f"2026-06-{30-i:02d}",
            "description": "一幅关于柔软日常与季节光线的习作。颜色像糖纸一样透亮，也保留了安静的呼吸感。",
            "thumb": f"/api/image/{i}/0?size=preview",
            "qualities": [
                {"id": "original", "label": "原始尺寸", "width": width, "height": height},
                {"id": "large", "label": "高清 75%", "width": round(width * .75), "height": round(height * .75)},
                {"id": "preview", "label": "预览 50%", "width": round(width * .5), "height": round(height * .5)}],
            "formats": [{"id": "source", "label": "保留源格式（推荐）"}, {"id": "svg", "label": "SVG"}]
        })
    return result
