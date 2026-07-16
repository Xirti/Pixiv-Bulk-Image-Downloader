from __future__ import annotations
import argparse,json,shutil,tempfile,time
from pathlib import Path
import websocket
from packaged_native_click_login_probe import (
    evaluate, free_port, launch, main_target, native_click, renderer_for, stop, wait_until,
)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--exe',required=True);args=parser.parse_args()
    exe=Path(args.exe).resolve();probe_root=Path(tempfile.mkdtemp(prefix='moku-packaged-help-'))
    process=None;ws=None
    result={'ok':False,'nativeClicks':[],'helpOpened':False,'guideBoundary':False,'networkHeadline':'','networkRoute':'','networkGuidance':'','checks':{},'processExited':False,'profilesAfterExit':-1,'error':''}
    try:
        debug_port=free_port();process,base,main_hwnd=launch(exe,probe_root,debug_port)
        target=wait_until(lambda:main_target(debug_port,base),25,'packaged main CDP target')
        ws=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=10,suppress_origin=True);counter=[0]
        wait_until(lambda:evaluate(ws,counter,"document.readyState === 'complete' && !!document.querySelector('#helpBtn')"),20,'packaged help page')
        geometry=evaluate(ws,counter,"""(() => {const r=document.querySelector('#helpBtn').getBoundingClientRect();return {viewport:{width:innerWidth,height:innerHeight},help:{x:r.x,y:r.y,width:r.width,height:r.height}}})()""")
        renderer=renderer_for(main_hwnd);result['nativeClicks'].append(native_click(renderer,geometry['viewport'],geometry['help']))
        result['helpOpened']=bool(wait_until(lambda:evaluate(ws,counter,"document.querySelector('#helpDialog')?.open === true"),10,'help dialog open'))
        details=evaluate(ws,counter,"""(() => {const d=document.querySelector('#helpDialog');const e=document.querySelector('#networkCheck');e.scrollIntoView({block:'center'});const b=e.getBoundingClientRect();const hit=document.elementFromPoint(b.x+b.width/2,b.y+b.height/2);return {button:{x:b.x,y:b.y,width:b.width,height:b.height},hitId:hit?.id||hit?.closest?.('button')?.id||'',boundary:d.textContent.includes('不会修改 Windows 系统代理')&&d.textContent.includes('不会自动启动 VPN')&&d.textContent.includes('不会扫描本机端口'),guide:d.textContent.includes('翻页仍保持完整标签组合')&&d.textContent.includes('不会提前下载未打开页面的缩略图')}})()""")
        result['guideBoundary']=bool(details['boundary'] and details['guide'])
        if details.get('hitId') != 'networkCheck':
            raise RuntimeError(f"network button is clipped; hit={details.get('hitId')!r}")
        time.sleep(.3)
        renderer=renderer_for(main_hwnd);result['nativeClicks'].append(native_click(renderer,geometry['viewport'],details['button']))
        wait_until(lambda:evaluate(ws,counter,"document.querySelector('#networkCheck')?.textContent === '重新检测网络'"),20,'network diagnosis complete')
        diagnosis=evaluate(ws,counter,"""(() => ({headline:document.querySelector('#networkHeadline').textContent,route:document.querySelector('#networkRoute').textContent,guidance:document.querySelector('#networkGuidance').textContent,pixiv:document.querySelector('#pixivCheck').textContent,cdn:document.querySelector('#cdnCheck').textContent}))()""")
        result['networkHeadline']=diagnosis['headline'];result['networkRoute']=diagnosis['route'];result['networkGuidance']=diagnosis['guidance'];result['checks']={'pixiv':diagnosis['pixiv'],'cdn':diagnosis['cdn']}
        result['ok']=result['helpOpened'] and result['guideBoundary'] and len(result['nativeClicks'])==2 and diagnosis['headline'] not in {'尚未检测','正在检查当前网络','网络检测未完成'} and all('检测中' not in diagnosis[k] and '待检测' not in diagnosis[k] for k in ('pixiv','cdn'))
        if not result['ok']: raise RuntimeError('packaged help/network assertions failed')
    except Exception as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
    finally:
        if ws is not None:
            try:ws.close()
            except Exception:pass
        stop(process,probe_root,remove_root=False)
        result['processExited']=bool(process is None or process.poll() is not None)
        sessions=list((probe_root/'localappdata'/'MOKU'/'WebView2Sessions').glob('session-*'))
        result['profilesAfterExit']=len(sessions)
        shutil.rmtree(probe_root,ignore_errors=True)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not (result['ok'] and result['processExited'] and result['profilesAfterExit']==0):raise SystemExit(1)
if __name__=='__main__':main()