"""App API boundary: normalized artworks, bounded search snapshots and artist reads."""
from collections import OrderedDict
from dataclasses import dataclass, field
import copy
import html
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from pixiv_adapter import (PixivPolicyError, valid_pixiv_id, is_allowed_pixiv_url,
    small_full_preview_url, publication_date, publication_timestamp, matches_tag_groups)
from search_service import (SearchInputError, SearchResultIndex, parse_search_query,
    parse_search_date_range, build_search_tag_groups)


def proxy_image(value):
    if not isinstance(value, str) or not is_allowed_pixiv_url(value, image_only=True):
        raise PixivPolicyError("App 图片地址无效")
    return "/api/pixiv/image?" + urlencode({"url": value})


def number(raw, key, *, minimum=0, optional=False):
    value = raw.get(key)
    if type(value) is not int or value < minimum:
        if optional:
            return None
        raise PixivPolicyError("App 作品字段无效：" + key)
    return value


def normalize_work(raw, *, allow_r18=False, detail=False, require_bookmarks=True):
    if not isinstance(raw, dict) or raw.get("visible") is not True:
        raise PixivPolicyError("App 作品当前不可见")
    restriction = number(raw, "x_restrict")
    if number(raw,"restrict") != 0 or restriction not in ({0, 1} if allow_r18 else {0}) or raw.get("is_muted") is True:
        raise PixivPolicyError("App 作品不在当前内容范围")
    uid, artist = raw.get("user"), None
    if isinstance(uid, dict):
        artist = str(uid.get("id", ""))
    identifier = str(raw.get("id", ""))
    if not valid_pixiv_id(identifier) or not artist or not valid_pixiv_id(artist):
        raise PixivPolicyError("App 作品身份无效")
    kind = raw.get("type")
    if kind not in {"illust", "manga", "ugoira"}:
        raise PixivPolicyError("App 作品类型不受支持")
    width, height, count = number(raw, "width", minimum=1), number(raw, "height", minimum=1), number(raw, "page_count", minimum=1)
    images, tags = raw.get("image_urls"), raw.get("tags")
    if not isinstance(images, dict) or not isinstance(tags, list):
        raise PixivPolicyError("App 图片或标签数据无效")
    source = images.get("medium") or images.get("square_medium")
    proxy_image(source)
    thumb = proxy_image(small_full_preview_url(source))
    translated = []
    for tag in tags:
        if not isinstance(tag, dict) or not isinstance(tag.get("name"), str):
            raise PixivPolicyError("App 标签格式异常")
        translated.append(tag["name"])
        if isinstance(tag.get("translated_name"), str):
            translated.append(tag["translated_name"])
    ai = raw.get("illust_ai_type")
    if type(ai) is not int or ai not in {0,1,2}:
        raise PixivPolicyError("App AI 标记异常")
    item = {"id": identifier, "source": "pixiv", "restriction": "r18" if restriction else "safe",
        "title": str(raw.get("title") or "未命名作品"), "artist": str(uid.get("name") or "未知画师"), "userId": artist,
        "pages": count, "width": width, "height": height, "tags": list(dict.fromkeys(translated)),
        "date": publication_date(raw.get("create_date")), "sortDate": publication_timestamp(raw.get("create_date")),
        "bookmarks": number(raw, "total_bookmarks", optional=not require_bookmarks), "description": html.unescape(re.sub(r"<[^>]+>", " ", str(raw.get("caption") or ""))),
        "workType": "illustration" if kind == "illust" else kind, "aiGenerated": ai == 2, "thumb": thumb,
        "qualities": [{"id":"original","label":"Pixiv 原图","width":width,"height":height},{"id":"regular","label":"Pixiv 常规预览","width":0,"height":0}],
        "formats": [{"id":"source","label":"保留源格式（推荐）"}]}
    if detail:
        pages = raw.get("meta_pages")
        if not isinstance(pages, list):
            raise PixivPolicyError("App 图片分页数据无效")
        if count > 1:
            if len(pages) != count:
                raise PixivPolicyError("App 图片页数不完整")
            normalized = []
            for page in pages:
                urls = page.get("image_urls") if isinstance(page, dict) else None
                if not isinstance(urls, dict):
                    raise PixivPolicyError("App 图片分页格式无效")
                normalized.append({"regular":proxy_image(urls.get("large") or urls.get("medium")),"original":proxy_image(urls.get("original")),"width":0,"height":0})
        else:
            single = raw.get("meta_single_page")
            if not isinstance(single, dict):
                raise PixivPolicyError("App 单图信息无效")
            original = single.get("original_image_url")
            normalized = [{"regular":proxy_image(images.get("large") or images.get("medium")),"original":proxy_image(original) if kind != "ugoira" else "","width":width,"height":height}]
        item["pageImages"] = normalized
        if kind == "ugoira":
            item["formats"] = [{"id":"source","label":"ZIP 帧包 + 帧延迟 JSON"},{"id":"gif","label":"GIF 动图"},{"id":"mp4","label":"MP4 视频（需本机 FFmpeg）"}]
    return item


def continuation(raw, path):
    value = raw.get("next_url")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise PixivPolicyError("App 续页格式无效")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc != "app-api.pixiv.net" or parsed.path != path or parsed.fragment:
        raise PixivPolicyError("App 续页地址不受信任")
    return parsed.path + "?" + parsed.query


def artist_profile(raw, details=None):
    if not isinstance(raw, dict) or not valid_pixiv_id(str(raw.get("id", ""))):
        raise PixivPolicyError("App 作者身份异常")
    images = raw.get("profile_image_urls")
    if not isinstance(images, dict):
        raise PixivPolicyError("App 作者头像格式异常")
    image = images.get("medium", "")
    details = details or {}
    background = details.get("background_image_url") or ""
    return {"userId":str(raw["id"]),"name":str(raw.get("name") or "未知画师"),
        "avatar":image if is_allowed_pixiv_url(image,image_only=True) else "",
        "background":background if is_allowed_pixiv_url(background,image_only=True) else "",
        "description":str(raw.get("comment") or ""),"region":str(details.get("region") or ""),"webpage":str(details.get("webpage") or "")}


@dataclass
class SearchSnapshot:
    sources: list
    index: SearchResultIndex = field(default_factory=SearchResultIndex)
    lock: threading.Lock = field(default_factory=threading.Lock)
    touched: float = field(default_factory=time.monotonic)
    invalid: bool = False


class AppGallery:
    def __init__(self, request, check):
        self.request, self.check = request, check
        self.sessions = OrderedDict()
        self.candidate_paths = OrderedDict()
        self.lock = threading.Lock()
        self.artist_reads = OrderedDict()

    def clear(self):
        with self.lock:
            for session in self.sessions.values():
                session.invalid = True
                if session.lock.acquire(blocking=False):
                    session.index.close();session.lock.release()
            self.sessions.clear()
            self.candidate_paths.clear()
            self.artist_reads.clear()

    def get(self, path, generation, cancel_event=None):
        self.check(generation, cancel_event)
        raw = self.request(path, lambda:self.check(generation,cancel_event))
        self.check(generation,cancel_event)
        return raw

    def detail(self, identifier, *, generation, allow_r18=False, cancel_event=None):
        raw = self.get('/v1/illust/detail?'+urlencode({'illust_id':identifier,'filter':'for_android'}),generation,cancel_event)
        item = normalize_work(raw.get('illust'),allow_r18=allow_r18,detail=True)
        if item['id'] != identifier:
            raise PixivPolicyError('App 返回的作品 ID 不匹配')
        return item

    def ugoira(self,identifier,*,generation,cancel_event=None):
        raw=self.get('/v1/ugoira/metadata?'+urlencode({'illust_id':identifier}),generation,cancel_event)
        meta=raw.get('ugoira_metadata')
        if not isinstance(meta,dict) or not isinstance(meta.get('zip_urls'),dict):raise PixivPolicyError('App 动图信息格式异常')
        src=meta['zip_urls'].get('medium');frames=meta.get('frames')
        proxy_image(src)
        if not isinstance(frames,list) or not frames or any(not isinstance(f,dict) or not isinstance(f.get('file'),str) or type(f.get('delay')) is not int or f['delay']<0 for f in frames):raise PixivPolicyError('App 动图帧信息异常')
        original=meta['zip_urls'].get('large') or meta['zip_urls'].get('original')
        if not original:
            if not src.endswith('_ugoira600x600.zip'):raise PixivPolicyError('App 动图原始档地址无法确认')
            original=src[:-len('600x600.zip')]+'1920x1080.zip'
        proxy_image(original)
        return {'src':src,'originalSrc':original,'mime':'image/jpeg','frames':frames}

    def artist(self, kind, target, *, generation, page=1, cancel_event=None, refresh=False):
        if kind == 'candidates':
            if not isinstance(target,str) or not 1 <= len(target) <= 60 or not 1 <= page <= 1000:
                raise SearchInputError('作者搜索条件无效')
            with self.lock:
                path='/v1/search/user?'+urlencode({'word':target,'filter':'for_android'}) if page==1 else self.candidate_paths.get((generation,target,page))
            if not path:raise SearchInputError('作者候选分页记录已清理，请从第一页重新搜索')
            raw = self.get(path,generation,cancel_event)
            rows = raw.get('user_previews')
            if not isinstance(rows,list):raise PixivPolicyError('App 作者候选格式异常')
            profiles = [artist_profile(row.get('user')) for row in rows if isinstance(row,dict)]
            next_path=continuation(raw,'/v1/search/user')
            if next_path:
                if next_path==path:raise PixivPolicyError('作者候选续页未前进')
                with self.lock:
                    self.candidate_paths[generation,target,page+1]=next_path
                    while len(self.candidate_paths)>80:self.candidate_paths.popitem(last=False)
            return {'query':target,'page':page,'candidates':[p for p in profiles if p['name'].casefold()==target.casefold()],'hasMore':bool(next_path)}
        if not valid_pixiv_id(target):raise SearchInputError('作者 ID 无效')
        key=(generation,target)
        with self.lock:
            entry=self.artist_reads.get(key)
            if entry is None or (entry['done'].is_set() and (refresh or time.monotonic()-entry['time']>120)):
                entry={'done':threading.Event(),'time':time.monotonic(),'raw':None,'error':None}
                self.artist_reads[key]=entry;owner=True
            else:owner=False
            self.artist_reads.move_to_end(key)
            while len(self.artist_reads)>64:self.artist_reads.popitem(last=False)
        if owner:
            try:entry['raw']=self.get('/v1/user/detail?'+urlencode({'user_id':target,'filter':'for_android'}),generation,cancel_event)
            except Exception as error:
                entry['error']=error
                with self.lock:
                    if self.artist_reads.get(key) is entry:self.artist_reads.pop(key)
                raise
            finally:entry['done'].set()
        else:
            while not entry['done'].wait(.05):self.check(generation,cancel_event)
            self.check(generation,cancel_event)
            if entry['error'] is not None:raise entry['error']
        raw=entry['raw']
        details = raw.get('profile')
        if not isinstance(details,dict):raise PixivPolicyError('App 作者资料格式异常')
        profile = artist_profile(raw.get('user'),details)
        if profile['userId'] != target:raise PixivPolicyError('App 作者身份不匹配')
        if kind == 'profile':return profile
        return {'userId':target,'counts':{'illusts':number(details,'total_illusts'),'manga':number(details,'total_manga')},'featured':[]}

    def search(self, tag, scope, page, work_type, include_ai, *, generation, fuzzy=False, start_date='',end_date='',snapshot_id='',cancel_event=None,prefetch=False,**unused):
        if scope not in {'safe','r18','all'} or work_type not in {'all','illustration','manga','ugoira'}:
            raise SearchInputError('搜索范围无效')
        page=max(1,int(page));query=parse_search_query(tag);dates=parse_search_date_range(start_date,end_date)
        groups=build_search_tag_groups(query.value,fuzzy=fuzzy) if query.kind=='tags' else ()
        if query.kind=='author':
            candidates=self.artist('candidates',query.value,generation=generation,cancel_event=cancel_event)['candidates']
            if len(candidates)!=1:raise SearchInputError('请先选择具体作者')
            query=parse_search_query('uid:'+candidates[0]['userId'])
        if query.kind=='pid':
            item=self.detail(query.value,generation=generation,allow_r18=scope!='safe',cancel_event=cancel_event)
            rows=[item] if (scope!='r18' or item['restriction']=='r18') and (include_ai or not item['aiGenerated']) and (work_type=='all' or item['workType']==work_type) and (not dates or dates.contains(item['date'])) else []
            return {'items':rows if page==1 else [],'page':page,'total':len(rows),'perPage':36,'hasMore':False,'availablePages':[1],'preloadedThrough':1,'snapshotId':snapshot_id,'tag':tag}
        key=(generation,query,scope,work_type,include_ai,groups,dates,snapshot_id)
        with self.lock:
            session=self.sessions.get(key)
            if session is None:
                if query.kind=='uid':
                    sources=[{'next':'/v1/user/illusts?'+urlencode({'user_id':query.value,'type':kind,'filter':'for_android'}),'bound':None} for kind in (['manga'] if work_type=='manga' else ['illust'] if work_type in {'illustration','ugoira'} else ['illust','manga'])]
                else:
                    sources=[]
                    for word in groups[0]:
                        params={'word':word,'sort':'date_desc','search_target':'exact_match_for_tags','filter':'for_android','merge_plain_keyword_results':'true'}
                        if not include_ai:params['search_ai_type']='1'
                        if dates:params.update(start_date=dates.start.isoformat(),end_date=dates.end.isoformat())
                        sources.append({'next':'/v1/search/illust?'+urlencode(params),'bound':None})
                session=SearchSnapshot(sources);self.sessions[key]=session
            self.sessions.move_to_end(key)
            for old_key,old in list(self.sessions.items()):
                if old_key!=key and (len(self.sessions)>8 or time.monotonic()-old.touched>1800):
                    old.invalid=True;del self.sessions[old_key]
                    if old.lock.acquire(blocking=False):old.index.close();old.lock.release()
        with session.lock:
            try:
                self.check(generation,cancel_event)
                if session.invalid:raise PixivPolicyError('搜索快照已清理，请刷新')
                session.touched=time.monotonic();requests=0;started=time.monotonic();target=page*36
                while session.index.emitted_count<target:
                    active=[s for s in session.sources if s['next']]
                    if active and any(s['bound'] is None for s in active):ready=[]
                    else:ready=session.index.take_ready(max((s['bound'] for s in active),default=None),target-session.index.emitted_count)
                    if ready:continue
                    if not active:break
                    if requests>=6 or time.monotonic()-started>40:break
                    source=max(active,key=lambda s:s['bound'] or ('9999',0))
                    path=source['next'];raw=self.get(path,generation,cancel_event);requests+=1
                    rows=raw.get('illusts')
                    if not isinstance(rows,list):raise PixivPolicyError('App 搜索格式异常')
                    next_path=continuation(raw,urlsplit(path).path)
                    if next_path==path:raise PixivPolicyError('App 续页未前进')
                    if query.kind=='uid' and next_path and parse_qs(urlsplit(next_path).query).get('user_id')!=[query.value]:
                        raise PixivPolicyError('App 作者续页身份不匹配')
                    normalized=[]
                    for row in rows:
                        try:item=normalize_work(row,allow_r18=scope!='safe')
                        except PixivPolicyError:continue
                        if query.kind=='uid' and item['userId']!=query.value:continue
                        if scope=='r18' and item['restriction']!='r18':continue
                        if work_type!='all' and item['workType']!=work_type:continue
                        if not include_ai and item['aiGenerated']:continue
                        if dates and not dates.contains(item['date']):continue
                        if groups and not matches_tag_groups(item['tags'],groups):continue
                        normalized.append(item)
                    self.check(generation,cancel_event)
                    session.index.add(normalized)
                    source['next']=next_path
                    if rows:
                        tail=rows[-1];stamp=publication_timestamp(tail.get('create_date')) if isinstance(tail,dict) else ''
                        source['bound']=(stamp,int(tail['id'])) if stamp and valid_pixiv_id(str(tail.get('id',''))) else None
                    elif next_path:raise PixivPolicyError('App 返回空续页，请刷新重试')
                self.check(generation,cancel_event)
                total=session.index.emitted_count;more=bool(session.index.pending_count or any(s['next'] for s in session.sources))
                rows=session.index.read_results((page-1)*36,page*36)
                pages=list(range(1,max(1,(total+35)//36)+1))
                return {'items':rows,'tag':tag,'page':page,'total':total,'perPage':36,'hasMore':more,'availablePages':pages,'preloadedThrough':max(pages),'snapshotId':snapshot_id,'budgetExhausted':more and len(rows)<36}
            finally:
                if session.invalid:session.index.close()
