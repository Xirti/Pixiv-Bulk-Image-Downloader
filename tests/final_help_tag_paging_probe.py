from __future__ import annotations
import json, shutil, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import websocket
import moku_app, server
from search_service import parse_search_tags

class CDP:
    def __init__(self, url): self.ws=websocket.create_connection(url,timeout=5); self.seq=0
    def call(self, method, params=None):
        self.seq+=1; rid=self.seq
        self.ws.send(json.dumps({'id':rid,'method':method,'params':params or {}}))
        while True:
            msg=json.loads(self.ws.recv())
            if msg.get('id')!=rid: continue
            if 'error' in msg: raise RuntimeError(msg['error'])
            return msg.get('result',{})
    def evaluate(self, expression):
        result=self.call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True})['result']
        if result.get('subtype')=='error': raise RuntimeError(result.get('description'))
        return result.get('value')
    def click(self, selector):
        quoted=json.dumps(selector)
        js='''(() => { const e=document.querySelector(%s); if(!e) return null; e.scrollIntoView({block:"center"}); const r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2}; })()''' % quoted
        rect=self.evaluate(js)
        if not rect: raise AssertionError('missing '+selector)
        for kind in ('mousePressed','mouseReleased'):
            self.call('Input.dispatchMouseEvent',{'type':kind,'x':rect['x'],'y':rect['y'],'button':'left','clickCount':1})
    def wait(self, expression, timeout=15):
        end=time.monotonic()+timeout; last=None
        while time.monotonic()<end:
            last=self.evaluate(expression)
            if last: return last
            time.sleep(.1)
        raise TimeoutError(f'{expression}; last={last!r}')
    def close(self): self.ws.close()

def opener(): return urllib.request.build_opener(urllib.request.ProxyHandler({}))

def main():
    searches=[]; probes=[]; image_indices=[]
    def fake_search(tag_query,scope,page,work_type,include_ai,*,authorized):
        tags=list(parse_search_tags(tag_query)); searches.append({'tag':tag_query,'page':page})
        start=(page-1)*36; items=[]
        for offset in range(36):
            index=start+offset; tag=tags[index%len(tags)]
            items.append({'id':str(900000+index),'title':f'{tag}-{index}','artist':'fixture','artistId':'1','tags':[tag],'pages':1,'width':1200,'height':900,'bookmarks':index,'date':'2026-07-15','description':'fixture','thumb':f'/api/image/{index}/0?size=preview','qualities':[],'formats':[],'source':'pixiv','restriction':'safe','workType':'illustration','aiGenerated':False})
        pages=list(range(1,page+4))
        return {'tag':' '.join(tags),'tags':tags,'scope':scope,'page':page,'pages':pages[-1],'pageNumbers':pages,'availablePages':pages,'preloadedThrough':pages[-1],'items':items,'perPage':36,'total':pages[-1]*36,'hasMore':True,'budgetExhausted':False,'truncatedDates':[],'workType':work_type,'includeAi':include_ai,'mode':'fixture'}
    def fake_request(url,image_only=False,*args,**kwargs):
        probes.append({'imageOnly':bool(image_only),'anonymous':bool(kwargs.get('anonymous'))})
        return (b'image','image/jpeg') if image_only else (b'<html></html>','text/html')
    original_svg=server.artwork_svg
    def tracked_svg(index,page,size): image_indices.append(int(index)); return original_svg(index,page,size)

    httpd=server.LocalThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True)
    profile=Path(tempfile.mkdtemp(prefix='moku-help-paging-')); browser=None; cdp=None
    patches=[patch.object(server,'search_pixiv_results',side_effect=fake_search),patch.object(server,'pixiv_request',side_effect=fake_request),patch.object(server,'artwork_svg',side_effect=tracked_svg),patch.object(server,'validated_session',return_value=False),patch.object(server,'auth_status_snapshot',return_value={'loggedIn':False,'state':'unauthorized'})]
    for item in patches: item.start()
    thread.start()
    try:
        app=f'http://127.0.0.1:{httpd.server_port}/'; edge=moku_app.find_edge()
        browser=subprocess.Popen([str(edge),'--headless=new','--disable-gpu','--window-size=1280,900',f'--user-data-dir={profile}','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--remote-allow-origins=*','--no-first-run','--no-default-browser-check',app],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        active=profile/'DevToolsActivePort'; end=time.monotonic()+15
        while time.monotonic()<end and not active.exists():
            if browser.poll() is not None: raise RuntimeError('Edge exited')
            time.sleep(.1)
        port=int(active.read_text(encoding='utf-8').splitlines()[0])
        targets=json.loads(opener().open(f'http://127.0.0.1:{port}/json/list',timeout=5).read())
        target=next(x for x in targets if x.get('type')=='page' and x.get('url','').startswith(app))
        cdp=CDP(target['webSocketDebuggerUrl']); cdp.call('Runtime.enable'); cdp.call('Page.enable'); cdp.wait('document.readyState === "complete"')
        assert probes==[]
        cdp.click('#helpBtn'); cdp.wait('document.querySelector("#helpDialog").open === true'); assert probes==[]
        cdp.click('#networkCheck'); cdp.wait('document.querySelector("#networkHeadline").textContent === "当前网络可以使用 Pixiv"')
        assert len(probes)==2 and all(x['anonymous'] for x in probes)
        cdp.evaluate('document.querySelector("#helpDialog").close(); document.querySelector("#tag").value="猫 夜景"; document.querySelector("#searchForm").requestSubmit(); true')
        cdp.wait('document.querySelectorAll("#grid .card").length === 36 && document.querySelector("#pagination .active")?.textContent === "1"')
        title1=cdp.evaluate('document.querySelector("#tagTitle").textContent'); src1=cdp.evaluate('[...document.querySelectorAll("#grid img")].map(x=>x.getAttribute("src"))')
        time.sleep(.5); before=list(image_indices)
        assert title1=='猫 + 夜景' and searches==[{'tag':'猫 夜景','page':1}]
        assert all(int(x.split('/api/image/')[1].split('/')[0])<36 for x in src1)
        assert not any(x>=36 for x in before)
        cdp.click('#pagination button[data-page="2"]')
        cdp.wait('document.querySelectorAll("#grid .card").length === 36 && document.querySelector("#pagination .active")?.textContent === "2"')
        title2=cdp.evaluate('document.querySelector("#tagTitle").textContent'); src2=cdp.evaluate('[...document.querySelectorAll("#grid img")].map(x=>x.getAttribute("src"))')
        end=time.monotonic()+5
        while time.monotonic()<end and not any(x>=36 for x in image_indices): time.sleep(.1)
        assert title2=='猫 + 夜景' and searches==[{'tag':'猫 夜景','page':1},{'tag':'猫 夜景','page':2}]
        assert all(int(x.split('/api/image/')[1].split('/')[0])>=36 for x in src2) and any(x>=36 for x in image_indices)
        print(json.dumps({'ok':True,'helpOpened':True,'networkRequestsBeforeClick':0,'anonymousNetworkChecks':len(probes),'firstTitle':title1,'secondTitle':title2,'searchCalls':searches,'pageTwoImageRequestedBeforeClick':any(x>=36 for x in before),'pageTwoImageRequestedAfterClick':any(x>=36 for x in image_indices),'pageTwoDomImages':len(src2)},ensure_ascii=False))
    finally:
        if cdp: cdp.close()
        if browser and browser.poll() is None:
            browser.terminate()
            try: browser.wait(8)
            except subprocess.TimeoutExpired: browser.kill(); browser.wait(5)
        httpd.shutdown(); httpd.server_close(); thread.join(5)
        for item in reversed(patches): item.stop()
        shutil.rmtree(profile,ignore_errors=True)
if __name__=='__main__': main()