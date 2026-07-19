from __future__ import annotations
import json, os, shutil, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request, winreg
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXE=Path(os.environ.get('MOKU_PROBE_EXE') or ROOT/'dist'/'MOKU'/'MOKU.exe').resolve()
LOCAL=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def proxy_snapshot():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
        def value(name,default):
            try:return winreg.QueryValueEx(key,name)[0]
            except OSError:return default
        return (int(value('ProxyEnable',0)),str(value('ProxyServer','')),str(value('AutoConfigURL','')))

def request(url,timeout,headers=None):
    outgoing=urllib.request.Request(url,headers=headers or {})
    try:
        with LOCAL.open(outgoing,timeout=timeout) as response:
            return response.status,response.read(),dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code,exc.read(),dict(exc.headers.items())

def request_json(url,timeout,headers=None):
    status,raw,response_headers=request(url,timeout,headers)
    try:data=json.loads(raw)
    except json.JSONDecodeError:data={'rawBytes':len(raw)}
    return status,data,response_headers

def search_url(base,page):
    query=urllib.parse.urlencode({'tag':'猫；犬','page':page,'mode':'safe','workType':'all','includeAi':'true','fuzzy':'false'})
    return base+'/api/pixiv/search?'+query

def match_counts(items):
    counts={'猫':0,'犬':0,'either':0,'neither':0}
    for item in items:
        tags={str(tag) for tag in item.get('tags') or []}
        for tag in ('猫','犬'):
            if tag in tags:counts[tag]+=1
        if tags.intersection({'猫','犬'}):counts['either']+=1
        else:counts['neither']+=1
    return counts

def main():
    if not EXE.is_file():raise FileNotFoundError(EXE)
    before=proxy_snapshot();root=Path(tempfile.mkdtemp(prefix='moku-packaged-tag-cache-'));runtime=root/'runtime';runtime.mkdir();descriptor=runtime/'backend.json'
    env=os.environ.copy();env.update({'MOKU_RUNTIME_DIR':str(runtime),'MOKU_MUTEX_NAME':'Local\\MOKU.TagCache.'+os.urandom(12).hex(),'MOKU_NO_BROWSER':'1','MOKU_DISABLE_PERSISTENT_SESSION':'1','MOKU_TEST_EXIT_AFTER_SECONDS':'300'})
    process=subprocess.Popen([str(EXE),'--serve-only'],cwd=EXE.parent,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    result={'ok':False,'pages':{},'tokenStatus':{},'proxySettingsUnchanged':False,'processExited':False}
    try:
        deadline=time.monotonic()+35;base=''
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError(f'EXE exited early: {process.returncode}')
            if descriptor.is_file():
                try:
                    row=json.loads(descriptor.read_text(encoding='utf-8-sig'));base=f"http://127.0.0.1:{int(row['port'])}"
                    status,health,_=request_json(base+'/api/health',2,{'Sec-Fetch-Site':'same-origin'})
                    if status==200 and health.get('instanceId')==row.get('instanceId'):break
                except (OSError,ValueError,KeyError,json.JSONDecodeError):pass
            time.sleep(.15)
        else:raise TimeoutError('packaged backend not healthy')
        protected_headers={'X-MOKU-Request-Token':str(health['requestToken']),'Sec-Fetch-Site':'same-origin'}
        account_status,account,_=request_json(base+'/api/status',10,protected_headers)
        if account_status!=200 or account.get("loggedIn") is not False:raise AssertionError('packaged probe must be isolated from persistent authorization')
        all_query=urllib.parse.urlencode({'tag':'猫','page':1,'mode':'all','workType':'all','includeAi':'true'})
        all_status,all_result,_=request_json(base+'/api/pixiv/search?'+all_query,10,protected_headers)
        if all_status != 403:raise AssertionError(f'all_status == 403 failed: {all_status} {all_result}')
        pages={};thumbs={}
        for page,timeout in ((1,120),(2,120),(8,180)):
            started=time.monotonic();status,data,_=request_json(search_url(base,page),timeout,protected_headers);elapsed=time.monotonic()-started
            if status!=200:raise AssertionError(f'page {page} status={status}: {data.get("error")}')
            items=data.get('items') or [];ids=[str(x.get('id')) for x in items]
            if data.get('tags')!=['猫','犬']:raise AssertionError(f'page {page} lost tags: {data.get("tags")}')
            if int(data.get('page') or 0)!=page:raise AssertionError(f'page mismatch: {data.get("page")}')
            if len(ids)>36 or len(ids)!=len(set(ids)):raise AssertionError(f'page {page} item count/duplicates: {len(ids)}/{len(set(ids))}')
            if page>1 and set(ids).intersection(*(set(pages[p]['ids']) for p in pages)):raise AssertionError(f'page {page} overlaps prior page')
            counts=match_counts(items)
            if counts['neither']!=0:raise AssertionError(f'page {page} has {counts["neither"]} items matching neither tag')
            pages[page]={'ids':ids,'elapsedSeconds':round(elapsed,3),'tags':data['tags'],'availablePages':data.get('availablePages'),'preloadedThrough':data.get('preloadedThrough'),'matches':counts}
            if not items:
                result['pages']={str(p):{k:v for k,v in row.items() if k!='ids'}|{'uniqueIds':len(set(row['ids']))} for p,row in pages.items()}
                result['emptyStrictAndResult']=True
                result['ok']=True
                break
            thumbs[page]=urllib.parse.urljoin(base+'/',str(items[0]['thumb']))
            if page==2:
                for source_page in (1,2):
                    image_status,raw,headers=request(thumbs[source_page],30)
                    result['tokenStatus'][f'page{source_page}AfterPage2']={'status':image_status,'bytes':len(raw),'cacheControl':headers.get('Cache-Control','')}
                    if image_status!=200 or headers.get('Cache-Control')!='no-store':raise AssertionError(f'page {source_page} preview invalid after page2')
        if len(thumbs)==3:
            status1,body1,_=request(thumbs[1],10);status2,body2,headers2=request(thumbs[2],30);status8,body8,headers8=request(thumbs[8],30)
            result['tokenStatus'].update({'page1AfterPage8':{'status':status1,'bytes':len(body1)},'page2AfterPage8':{'status':status2,'bytes':len(body2),'cacheControl':headers2.get('Cache-Control','')},'page8AfterPage8':{'status':status8,'bytes':len(body8),'cacheControl':headers8.get('Cache-Control','')}})
            if status1!=403:raise AssertionError(f'page1 token was not evicted: {status1}')
            if status2!=200 or headers2.get('Cache-Control')!='no-store':raise AssertionError(f'page2 token should remain: {status2}')
            if status8!=200 or headers8.get('Cache-Control')!='no-store':raise AssertionError(f'page8 token should work: {status8}')
            result['pages']={str(p):{k:v for k,v in row.items() if k!='ids'}|{'uniqueIds':len(set(row['ids']))} for p,row in pages.items()}
            result['ok']=True
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(10)
            except subprocess.TimeoutExpired:process.kill();process.wait(5)
        result['processExited']=process.poll() is not None
        result['proxySettingsUnchanged']=proxy_snapshot()==before
        shutil.rmtree(root,ignore_errors=True)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not (result['ok'] and result['processExited'] and result['proxySettingsUnchanged']):raise SystemExit(1)
if __name__=='__main__':main()