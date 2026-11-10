"""SauceNAO result boundary: provider records, public links and optional Pixiv IDs."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import math
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

MAX_RESULTS = 64


class SourceSearchError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def web_link(value):
    """Public webpage destinations only; also used at the native-open boundary."""
    if not isinstance(value,str) or len(value)>4096:return ""
    value=html.unescape(value.strip())
    if re.search(r"[\x00-\x20\x7f\\]",value):return ""
    try:
        parsed=urlsplit(value)
        if (parsed.scheme not in {"http","https"} or parsed.username or parsed.password
                or parsed.port not in {None,80,443} or not parsed.hostname):return ""
        host=parsed.hostname.encode("idna").decode("ascii").lower()
    except (ValueError,UnicodeError):return ""
    try:
        address=ipaddress.ip_address(host)
    except ValueError:
        if (len(host)>253 or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}",host)
                or host.endswith((".localhost",".local",".internal",".invalid",".test"))):return ""
        # Browsers parse numeric-ending hosts as IPv4, including octal/hex
        # spellings that ipaddress correctly refuses as canonical addresses.
        if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)",host.rsplit(".",1)[-1]):return ""
    else:
        if not address.is_global:return ""
        host="["+host+"]" if address.version==6 else host
    port=":"+str(parsed.port) if parsed.port is not None else ""
    return urlunsplit((parsed.scheme,host+port,parsed.path or "/",parsed.query,parsed.fragment))


def pixiv_id(url):
    try:parsed=urlsplit(url)
    except ValueError:return ""
    if parsed.scheme not in {"http","https"} or parsed.username or parsed.password:return ""
    if parsed.hostname=="i.pximg.net":
        match=re.fullmatch(r"/(?:img-original|img-master)/img/(?:[0-9]{2,4}/){6}([1-9][0-9]{0,29})(?:_p[0-9]+(?:_master[0-9]+)?\.(?:jpg|jpeg|png|gif|webp))?",parsed.path)
        return match[1] if match else ""
    if parsed.hostname not in {"pixiv.net","www.pixiv.net"}:return ""
    match=re.fullmatch(r"/(?:[a-z]{2}/)?artworks/([1-9][0-9]{0,29})/?",parsed.path)
    value=match[1] if match else parse_qs(parsed.query).get("illust_id",[""])[0] if parsed.path=="/member_illust.php" else ""
    return value if re.fullmatch(r"[1-9][0-9]{0,29}",value) else ""


def thumbnail_url(value):
    if not isinstance(value,str) or len(value)>2048:return ""
    try:
        parsed=urlsplit(value)
        allowed=parsed.scheme=="https" and re.fullmatch(r"(?:img[0-9]+\.)?saucenao\.com",parsed.hostname or "")
        safe=not parsed.username and not parsed.password and parsed.port in {None,443}
    except ValueError:return ""
    return value if allowed and safe and not parsed.path.lower().endswith((".php",".html")) else ""


def text(value,limit=200):
    if value is None:return ""
    if not isinstance(value,str):raise SourceSearchError("来源文字格式无效",502)
    return " ".join(html.unescape(value).split())[:limit]


def site_name(url):
    host=urlsplit(url).hostname.removeprefix("www.")
    return {"pixiv.net":"Pixiv","i.pximg.net":"Pixiv 图片","x.com":"X","twitter.com":"X",
            "danbooru.donmai.us":"Danbooru","gelbooru.com":"Gelbooru","deviantart.com":"DeviantArt",
            "artstation.com":"ArtStation","anidb.net":"AniDB","myanimelist.net":"MyAnimeList"}.get(host,host)


def normalize_candidates(rows):
    if not isinstance(rows,list) or len(rows)>MAX_RESULTS:raise SourceSearchError("搜图结果格式无效",502)
    results={}
    for row in rows:
        if not isinstance(row,dict):raise SourceSearchError("来源记录格式无效",502)
        urls=row.get("urls",[])
        labels=row.get("linkLabels",{})
        if (not isinstance(urls,list) or len(urls)>64 or not all(isinstance(url,str) for url in urls)
                or not isinstance(labels,dict) or len(labels)>64 or not all(isinstance(k,str) and isinstance(v,str) for k,v in labels.items())):
            raise SourceSearchError("来源链接格式无效",502)
        links={}
        for raw in urls:
            url=web_link(raw)
            if not url:continue
            host=urlsplit(url).hostname
            if host=="saucenao.com" or host.endswith(".saucenao.com"):continue
            links.setdefault(url,{"url":url,"site":site_name(url),"label":text(labels.get(raw,""),100),
                "pixivId":pixiv_id(url) if host in {"pixiv.net","www.pixiv.net"} else ""})
        ids=list(dict.fromkeys(pid for link in links for pid in [pixiv_id(link)] if pid))
        value=row.get("pid","")
        if type(value) in {str,int} and re.fullmatch(r"[1-9][0-9]{0,29}",str(value)) and str(value) not in ids:ids.append(str(value))
        for pid in ids:
            url="https://www.pixiv.net/artworks/"+pid
            links.setdefault(url,{"url":url,"site":"Pixiv","label":"","pixivId":pid})
        if isinstance(row.get("similarity"),bool):raise SourceSearchError("来源相似度无效",502)
        try:similarity=float(row.get("similarity",0))
        except (TypeError,ValueError):raise SourceSearchError("来源相似度无效",502) from None
        if not math.isfinite(similarity) or not 0<=similarity<=100:raise SourceSearchError("来源相似度无效",502)
        title,author,index=text(row.get("title")),text(row.get("author"),200),text(row.get("indexName"),120)
        details=row.get("details",[])
        if not isinstance(details,list) or len(details)>24:raise SourceSearchError("来源说明格式无效",502)
        details=list(dict.fromkeys(value for raw in details if (value:=text(raw,1200))))
        hidden=row.get("hidden")
        restriction="r18" if type(hidden) is int and hidden>0 else "safe" if type(hidden) is int and hidden==0 else "unknown"
        thumb=thumbnail_url(row.get("thumbnail"))
        # Keep distinct provider entries, even when an author or a generic ID
        # overlaps. Only identical source records are deduplicated.
        identity=json.dumps([index,title,author,sorted(links),thumb,details],ensure_ascii=False,separators=(",",":"))
        key=hashlib.sha256(identity.encode()).hexdigest()[:24]
        candidate={"id":"source-"+key,"title":title,"artist":author,"indexName":index,
                   "similarity":round(similarity,2),"thumbnail":thumb,"pixivIds":ids,"links":list(links.values()),
                   "details":details,"restriction":restriction,"sensitive":restriction=="r18"}
        previous=results.get(key)
        if previous:
            if previous["restriction"]=="r18" or restriction=="r18":candidate.update(restriction="r18",sensitive=True)
            if previous["similarity"]>=similarity:
                previous.update(restriction=candidate["restriction"],sensitive=candidate["sensitive"]);continue
        results[key]=candidate
    return sorted(results.values(),key=lambda row:row["similarity"],reverse=True)


def parse_api(payload):
    if not isinstance(payload,dict) or not isinstance(payload.get("header"),dict) or not isinstance(payload.get("results",[]),list):
        raise SourceSearchError("SauceNAO 没有返回有效的 JSON 搜索结果",502)
    header=payload["header"];quota={}
    for name in ("short_limit","long_limit","short_remaining","long_remaining"):
        value=header.get(name)
        if value is None:continue
        try:count=int(value)
        except (TypeError,ValueError,OverflowError):raise SourceSearchError("服务商额度格式无效",502) from None
        if not -1<=count<=10_000_000:raise SourceSearchError("服务商额度格式无效",502)
        quota[name]=count
    try:status=int(header.get("status",0))
    except (TypeError,ValueError,OverflowError):raise SourceSearchError("服务商状态格式无效",502) from None
    if status:
        message=str(header.get("message","")).lower()
        if "anonymous account" in message:raise SourceSearchError("SauceNAO 将本次请求识别为匿名账号，请检查 API Key 是否有效及接口是否识别该 Key",401)
        if "limit" in message or "quota" in message:raise SourceSearchError("SauceNAO 查询额度不足，请稍后再试",429)
        if "key" in message or "access" in message:raise SourceSearchError("API Key 无效或账号没有 API 权限，请检查配置",401)
        raise SourceSearchError("SauceNAO 未完成查询，请检查账号或稍后重试",502)
    rows=[]
    for result in payload.get("results",[])[:MAX_RESULTS]:
        if not isinstance(result,dict) or not isinstance(result.get("header"),dict) or not isinstance(result.get("data"),dict):
            raise SourceSearchError("SauceNAO 来源记录格式无效",502)
        info,data=result["header"],result["data"];urls=data.get("ext_urls",[])
        if not isinstance(urls,list):raise SourceSearchError("来源链接格式无效",502)
        source=data.get("source")
        source_is_link=isinstance(source,str) and source.lstrip().lower().startswith(("http://","https://"))
        for key in ("source","author_url","url","post_url"):
            if isinstance(data.get(key),str) and (key!="source" or source_is_link):urls=[*urls,data[key]]
        title=data.get("title") or data.get("eng_name") or data.get("jp_name") or (source if isinstance(source,str) and not source_is_link else "")
        author=data.get("member_name") or data.get("author_name") or data.get("creator") or data.get("twitter_user_handle") or ""
        if isinstance(author,list):author=" / ".join(text(value,100) for value in author[:12])
        details=[]
        if isinstance(source,str) and not source_is_link and source and source!=title:details.append("来源："+source)
        for key,label in [("material","作品"),("characters","角色"),("part","部分"),("est_time","时间"),("year","年份")]:
            value=data.get(key)
            if isinstance(value,list):value=" / ".join(text(part,100) for part in value[:12])
            if type(value) in {str,int,float} and str(value).strip():details.append(label+"："+str(value))
        labels={data["author_url"]:"作者主页"} if isinstance(data.get("author_url"),str) else {}
        rows.append({"pid":data.get("pixiv_id",""),"urls":urls,"linkLabels":labels,"title":title,"author":author,
            "indexName":info.get("index_name"),"similarity":info.get("similarity",0),
            "thumbnail":info.get("thumbnail",""),"hidden":info.get("hidden"),"details":details})
    return normalize_candidates(rows),quota
