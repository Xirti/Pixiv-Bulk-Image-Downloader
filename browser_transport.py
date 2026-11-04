"""Browser-compatible HTTP transport with one shared async connection pool.

The caller owns URL policy, pacing and account authorization. Each transfer writes
bounded chunks directly to caller-owned storage. The sync bridge polls cancellation
while libcurl is connecting, waiting for headers, or receiving a body.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from email.message import Message
import http.client
import logging
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse

from curl_cffi import CurlOpt, CurlInfo
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from pixiv_adapter import PixivPolicyError
from request_coordinator import RequestBlockedError

from browser_profile import BrowserProfile, load_browser_profile
from download_history import history_path

LOG = logging.getLogger("moku.http")


class BrowserTransport:
    def __init__(self, proxy: str = "", *, profile: BrowserProfile | None = None):
        self.proxy = proxy
        self.configuration = profile if profile is not None else load_browser_profile(history_path().with_name("browser-profile.json"))
        self.profile = self.configuration.name
        self._lock = threading.Lock()
        self._active = 0
        self._retired = False
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="moku-browser-network", daemon=True)
        self._thread.start()
        self._ready.wait()
        initialization = asyncio.run_coroutine_threadsafe(self._create_session(), self._loop)
        try:
            self._session = initialization.result(timeout=10)
        except BaseException:
            initialization.cancel()
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            raise

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        self._loop.close()

    async def _create_session(self):
        return AsyncSession(
            impersonate=self.profile, max_clients=8, trust_env=False,
            allow_redirects=False, retry=0,
            extra_fp={"tls_permute_extensions": True},
            curl_options={CurlOpt.SSL_OPTIONS: 16, CurlOpt.MAXCONNECTS: 8},
            curl_infos=[CurlInfo.NUM_CONNECTS],
        )

    async def _transfer(self, url, headers, output, maximum, timeout, duration, check, on_chunk=None):
        chunks = []
        received = 0
        callback_error = None
        prefix = bytearray()

        def consume(chunk):
            nonlocal received, callback_error
            try:
                check()
                if received + len(chunk) > maximum:
                    raise PixivPolicyError("远程响应过大")
                if output is None:
                    chunks.append(chunk)
                else:
                    output.write(chunk)
                if len(prefix) < 16384:
                    prefix.extend(chunk[:16384 - len(prefix)])
                received += len(chunk)
                if on_chunk is not None:
                    on_chunk(len(chunk))
                return len(chunk)
            except Exception as exc:
                # libcurl reports callback abort as a transport error. Preserve
                # the actual cancellation, file-write, or size-limit failure.
                callback_error = exc
                return 0

        request_headers = self.configuration.headers(url, headers)
        try:
            response = await self._session.get(
                url, headers=request_headers, proxy=self.proxy or None,
                content_callback=consume, timeout=(timeout, duration),
                discard_cookies=True, allow_redirects=False,
            )
        except RequestException as exc:
            if callback_error is not None:
                raise callback_error from exc
            code = int(exc.code)
            if code in {60, 77, 83, 90}:
                raise ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED") from exc
            if code == 28:
                raise TimeoutError("Pixiv transfer timed out") from exc
            if code in {5, 6}:
                raise socket.gaierror(socket.EAI_AGAIN, "DNS lookup failed") from exc
            if code == 7:
                raise ConnectionRefusedError("Pixiv connection refused") from exc
            if code in {35, 51, 58, 59, 64}:
                raise ssl.SSLError("TLS_HANDSHAKE_FAILED") from exc
            raise ConnectionError(f"Pixiv transfer interrupted (CURL_{code})") from exc
        if callback_error is not None:
            raise callback_error
        LOG.log(
            logging.INFO if response.status_code >= 400 else logging.DEBUG,
            "Pixiv response profile=%s revision=%s host=%s status=%s http=%s new_connections=%s ms=%d",
            self.profile, self.configuration.revision, urllib.parse.urlsplit(url).hostname,
            response.status_code, response.http_version, response.infos[CurlInfo.NUM_CONNECTS],
            round(response.elapsed.total_seconds() * 1000),
        )
        message = Message()
        for key, value in response.headers.multi_items():
            message.add_header(key, value)
        if message.get("cf-mitigated") == "challenge" or (
            message.get_content_type() == "text/html"
            and (b"/cdn-cgi/challenge-platform/" in prefix.lower() or b"cf-chl-" in prefix.lower())
        ):
            raise RequestBlockedError("verification_required")
        if 300 <= response.status_code < 400:
            raise PixivPolicyError("Pixiv 响应包含重定向，已按安全策略拒绝")
        if response.status_code >= 400:
            raise urllib.error.HTTPError(url, response.status_code, "Pixiv upstream response", message, None)
        if response.status_code == 206 or message.get("Content-Range"):
            raise PixivPolicyError("Pixiv 返回了局部内容，请重新下载完整文件")
        # Curl decodes content encodings. Content-Length describes encoded bytes.
        declared = message.get("Content-Length")
        if declared and not message.get("Content-Encoding"):
            try:
                expected = int(declared)
            except ValueError as exc:
                raise PixivPolicyError("Pixiv 返回了无效的 Content-Length") from exc
            if expected != received:
                raise http.client.IncompleteRead(b"", expected - received)
        return b"".join(chunks) if output is None else b"", message.get_content_type()

    def fetch(self, url, headers, *, output, maximum, timeout, deadline, check, before_send, on_chunk=None):
        check()
        before_send()
        with self._lock:
            if self._retired:
                raise ConnectionError("Browser connection pool retired")
            self._active += 1
        future = None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._transfer(url, headers, output, maximum, timeout, max(.1, deadline - time.monotonic()), check, on_chunk),
                self._loop,
            )
            while True:
                check()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Pixiv response deadline exceeded")
                try:
                    return future.result(timeout=.05)
                except FutureTimeoutError:
                    if future.done():
                        return future.result()
        finally:
            if future is not None and not future.done():
                future.cancel()
                # Fence cancellation before the caller releases its output file.
                asyncio.run_coroutine_threadsafe(self._fence(), self._loop).result(timeout=5)
            with self._lock:
                self._active -= 1
                close = self._retired and self._active == 0
            if close:
                self._shutdown()

    async def _fence(self):
        # Cancellation is posted before this coroutine. Let the cancelled request
        # remove its libcurl handle and finish any in-progress Python callback.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    def _shutdown(self):
        asyncio.run_coroutine_threadsafe(self._session.close(), self._loop).result(timeout=5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def close(self):
        with self._lock:
            if self._retired:
                return
            self._retired = True
            close = self._active == 0
        if close:
            self._shutdown()
