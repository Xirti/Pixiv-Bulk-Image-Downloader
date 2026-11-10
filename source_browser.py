"""Isolated, user-verifiable SauceNAO browser. No Pixiv or desktop API bridge."""
from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import queue
import time
import urllib.parse
import urllib.request


READ_RESULT = r"""(() => {
  if (location.origin !== 'https://saucenao.com') return {kind:'other'};
  const text = (document.body?.innerText || '').slice(0,262144);
  if (/just a moment/i.test(document.title) || /verify you are human|performing security verification/i.test(text)) return {kind:'verification'};
  if (!['/', '/index.php', '/search.php'].includes(location.pathname)) return {kind:'other'};
  if (text.trimStart().startsWith('{')) {
    try { return {kind:'result',json:JSON.parse(text)}; } catch { return {kind:'error'}; }
  }
  const blocks = [...document.querySelectorAll('.result')].slice(0,64);
  if (blocks.length || /sauce found/i.test(document.title)) {
    if (/search limit|limit exceeded|too many requests/i.test(text)) return {kind:'error'};
    const rows = blocks.map(block => {
      const anchors=[...block.querySelectorAll('.resultcontent a[href], .resultmiscinfo a[href], .resultimage a[href]')].slice(0,64);
      const urls=anchors.map(a=>a.href),linkLabels=Object.fromEntries(anchors.map(a=>[a.href,(a.textContent || '').trim().slice(0,100)]));
      const score=block.querySelector('.resultsimilarity')?.textContent || '';
      const title=block.querySelector('.resulttitle')?.textContent || '';
      const content=(block.querySelector('.resultcontent')?.innerText || '').split('\n').map(line=>line.trim()).filter(Boolean);
      const authorAt=content.findIndex(line=>/^(?:creator|author|artist|member|作者|作家)\s*[:：]/i.test(line));
      const author=authorAt<0?'':content[authorAt].replace(/^[^:：]+[:：]\s*/,'') || (content[authorAt+1] && !/[:：]$/.test(content[authorAt+1])?content[authorAt+1]:'');
      const indexName=block.querySelector('.resultindex')?.textContent || block.getAttribute('data-index-name') || '';
      const image=block.querySelector('.resultimage img');
      const hidden=block.matches('.hidden, .result-hidden') || image?.matches('.hidden, .blurred') || /blur\(/i.test(image?.getAttribute('style') || '') ? 1 : null;
      return {urls,linkLabels,title,author,indexName,details:content.filter((line,index)=>line!==title && line!==author && index!==authorAt).slice(0,24).map(line=>line.slice(0,1200)),hidden,
        similarity:parseFloat(score) || 0,thumbnail:image?.src || ''};
    });
    return {kind:'result',rows};
  }
  if (/limit exceeded|daily search limit|invalid image|image.*too small/i.test(text)) return {kind:'error'};
  return {kind:'home'};
})()"""

READ_LOGIN = r"""(() => {
  if(location.origin!=='https://saucenao.com') return 'unknown';
  const logout=[...document.querySelectorAll('a[href]')].some(a=>{
    const url=new URL(a.href);
    return url.origin===location.origin && /logout|log_out/i.test(url.pathname+url.search);
  });
  if(logout || document.querySelector('input[name="logout"]')) return 'signed-in';
  return document.querySelector('input[type="password"]')?'signed-out':'unknown';
})()"""


def run():
    import webview
    from webview.menu import Menu, MenuAction

    backend = os.environ.pop("MOKU_SOURCE_BACKEND", "")
    identity = os.environ.pop("MOKU_SOURCE_JOB", "")
    secret = os.environ.pop("MOKU_SOURCE_SECRET", "")
    profile = os.environ.pop("MOKU_SOURCE_PROFILE", "")
    proxy = os.environ.pop("MOKU_SOURCE_PROXY", "")
    parsed = urllib.parse.urlsplit(backend)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.path not in {"", "/"}:
        raise ValueError("Invalid source-search backend")
    if not identity or not secret or not profile:
        raise ValueError("Missing source-search context")
    # Use only the explicitly selected loopback proxy, never inherit a profile
    # belonging to the main application or a user's system browser.
    os.environ.pop("WEBVIEW2_USER_DATA_FOLDER", None)
    os.environ.pop("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", None)
    if proxy:
        from network_config import normalize_loopback_proxy
        proxy = normalize_loopback_proxy(proxy)
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--proxy-server=" + proxy
    os.environ["PYTHONNET_RUNTIME"] = "netfx"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def send(action, **data):
        raw = json.dumps({"id": identity, "action": action, **data}).encode()
        request = urllib.request.Request(backend.rstrip("/") + "/api/source-search/browser", data=raw,
                                        headers={"Content-Type": "application/json", "X-MOKU-Source-Capability": secret})
        with opener.open(request, timeout=8) as response:
            return json.loads(response.read(3 * 1024 * 1024))

    payload = send("payload")
    if payload["state"] in {"cancelled", "error", "done"}:
        return
    encoded = payload["image"]
    mode = payload.get("mode", "guest")
    post_stream = None
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    window = webview.create_window("MOKU — SauceNAO · " + ({"key":"API 验证","login":"官网登录","guest":"官网查询"}[mode]), html="<meta charset='utf-8'><p>正在打开 SauceNAO 官网…</p>", width=1020, height=780)
    commands = queue.Queue(maxsize=8)
    submitted = False
    completed = False
    last_kind = ""
    automatic_submit = True
    login_status = "unknown"
    start_url = "https://saucenao.com/user.php" if mode == "login" else "https://saucenao.com/"

    def schedule(action):
        try:
            commands.put_nowait(action)
        except queue.Full:
            pass  # Repeated menu/loaded notifications share a bounded queue.

    def submit():
        nonlocal submitted, post_stream, automatic_submit
        if completed or window.events.closed.is_set():
            return
        if mode == "login":
            window.set_title("MOKU — 请在官网完成登录，再回主界面查询")
            return
        result = window.evaluate_js(READ_RESULT)
        if result.get("kind") == "verification":
            send("verification")
            window.set_title("MOKU — 请手动通过验证，然后点「提交查询」")
            return
        if mode == "key":
            if result.get("kind") == "other":
                submitted = False
                automatic_submit = True
                window.load_url("https://saucenao.com/")
                return
            try:
                request = send("api-request")
            except urllib.error.HTTPError:
                window.set_title("MOKU — API 请求等待中或暂不能提交，请查看主界面提示")
                return
            from System import Action, Array, Byte
            from System.IO import MemoryStream
            if post_stream is not None:
                post_stream.Dispose()
            post_stream = MemoryStream(Array[Byte](base64.b64decode(request["body"])))
            destination = urllib.parse.urlsplit(request["url"])
            if destination.scheme != "https" or destination.netloc != "saucenao.com" or destination.path != "/search.php":
                raise ValueError("Invalid API destination")
            def navigate_api():
                core = window.native.webview.CoreWebView2
                native_request = core.Environment.CreateWebResourceRequest(
                    request["url"], "POST", post_stream,
                    "Content-Type: " + request["contentType"] + "\r\nAccept: application/json\r\n")
                core.NavigateWithWebResourceRequest(native_request)
            submitted = True
            send("submitted")
            window.native.Invoke(Action(navigate_api))
            window.set_title("MOKU — API Key 查询中，请等待 JSON 结果")
            return
        script = """(() => {
              if (location.origin!=='https://saucenao.com' || !['/', '/index.php', '/search.php'].includes(location.pathname)) return false;
              if (!document.querySelector('input[type=file]')) return false;
              const form=document.createElement('form');form.method='POST';form.action='https://saucenao.com/search.php';form.enctype='multipart/form-data';
              const input=document.createElement('input');input.type='file';input.name='file';form.append(input);
              for(const [name,value] of [['db','999'],['numres','16'],['output_type','0']]){
                const field=document.createElement('input');field.type='hidden';field.name=name;field.value=value;form.append(field);
              }
              const bytes=Uint8Array.from(atob(ENCODED),c=>c.charCodeAt(0));
              const transfer=new DataTransfer();transfer.items.add(new File([bytes],'query.jpg',{type:'image/jpeg'}));input.files=transfer.files;
              form.hidden=true;document.body.append(form);form.submit();return true;
            })()""".replace("ENCODED", json.dumps(encoded))
        if not window.evaluate_js(script):
            window.set_title("MOKU — 官网尚未准备好，可刷新后点「提交查询」")
            return
        submitted = True
        send("submitted")
        window.set_title("MOKU — 正在搜图，请勿重复提交")

    def observe():
        nonlocal completed, last_kind, encoded, automatic_submit, login_status
        auto_submit = False
        try:
            if completed:
                return
            session = window.evaluate_js(READ_LOGIN)
            if session in ("signed-in", "signed-out") and session != login_status:
                send("session", login=session)
                login_status = session
            if mode == "login":
                if session == "signed-in":
                    completed = True
                    window.set_title("MOKU — SauceNAO 官网已登录，可回主界面查询")
                return
            result = window.evaluate_js(READ_RESULT)
            kind = result.get("kind")
            if kind == "verification":
                if last_kind != kind:
                    send("verification")
                    window.set_title("MOKU — 验证请手动完成，再点「提交查询」")
            elif kind == "result" and submitted:
                args = {"json": result["json"]} if "json" in result else {"rows": result["rows"]}
                response = send("result", **args)
                completed = response.get("state") in {"done", "error", "cancelled"}
                if completed:
                    encoded = ""
                window.set_title("MOKU — API 返回错误，请查看主界面" if response.get("state") == "error" else "MOKU — 结果已带回主界面，可关闭此窗口")
            elif kind == "error":
                send("error")
                completed = True
                encoded = ""
                window.set_title("MOKU — 查询未完成，请查看官网提示")
            elif kind == "home" and not submitted and automatic_submit:
                automatic_submit = False
                auto_submit = True
            last_kind = kind
        except Exception:
            # This is the visible browser boundary: keep the official page open
            # for manual recovery; do not silently claim a successful search.
            window.set_title("MOKU — 读取结果失败，可点「读取当前结果」重试")
        if auto_submit:
            submit()

    def startup():
        nonlocal identity, secret, encoded, submitted, completed, last_kind, mode, automatic_submit, login_status
        if not window.events.loaded.wait(25):
            send("error")
            return
        from System import Action

        def navigate():
            core = window.native.webview.CoreWebView2

            def navigation_completed(sender, args):
                if not args.IsSuccess and str(args.WebErrorStatus) != "OperationCanceled":
                    schedule("connection-error")

            core.NavigationCompleted += navigation_completed
            core.Navigate(start_url)

        window.native.Invoke(Action(navigate))
        deadline = time.monotonic() + 600
        next_poll = 0
        while not window.events.closed.wait(.25):
            try:
                action = commands.get_nowait()
            except queue.Empty:
                action = None
            try:
                if action == "connection-error":
                    send(action)
                    completed = True
                    window.set_title("MOKU — 官网连接失败，可从主界面重新查询")
                elif action == "submit":
                    submit()
                elif action == "observe":
                    observe()
                elif action == "home":
                    window.load_url("https://saucenao.com/")
                elif action == "login":
                    automatic_submit = False
                    completed = False
                    login_status = "unknown"
                    window.load_url("https://saucenao.com/user.php")
                if time.monotonic() < next_poll:
                    continue
                next_poll = time.monotonic() + 2
                response = send("poll")
                pending = response.get("next")
                if pending:
                    identity, secret = pending["id"], pending["secret"]
                    payload = send("payload")
                    encoded = payload["image"]
                    mode = payload.get("mode", "guest")
                    login_status = "unknown"
                    submitted = completed = False
                    automatic_submit = True
                    last_kind = ""
                    deadline = time.monotonic() + 600
                    window.set_title("MOKU — SauceNAO · " + {"key":"API 验证","login":"官网登录","guest":"官网查询"}[mode])
                    window.show()
                    window.load_url("https://saucenao.com/user.php" if mode == "login" else "https://saucenao.com/")
                    continue
                state = response["state"]
            except (OSError, ValueError):
                window.destroy()
                return
            if state == "cancelled":
                window.destroy()
                return
            if not completed and time.monotonic() > deadline:
                send("error")
                completed = True
                encoded = ""
                window.set_title("MOKU — 查询超时，可关闭窗口后重试")
                deadline = float("inf")
            if not completed:
                observe()
        try:
            send("closed")
        except OSError:
            pass  # The owning backend may already be shut down.

    window.events.loaded += lambda: schedule("observe")
    Path(profile).mkdir(parents=True, exist_ok=True)
    menu = [Menu("SauceNAO", [MenuAction("登录 / 账号", lambda: schedule("login")),
                              MenuAction("提交查询（验证后使用）", lambda: schedule("submit")),
                              MenuAction("读取当前结果", lambda: schedule("observe")),
                              MenuAction("打开官网首页", lambda: schedule("home")),
                              MenuAction("关闭", window.destroy)])]
    webview.start(startup, gui="edgechromium", private_mode=False, storage_path=profile, menu=menu, debug=False)
