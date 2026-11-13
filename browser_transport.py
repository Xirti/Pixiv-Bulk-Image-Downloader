"""Browser-compatible HTTP transport with one shared async connection pool.

The caller owns URL policy, pacing and account authorization. Each transfer writes
bounded chunks directly to caller-owned storage. The sync bridge polls cancellation
while libcurl is connecting, waiting for headers, or receiving a body.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
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


class TransportClosedError(ConnectionError):
    pass


@dataclass(eq=False)
class _AsyncRequest:
    result: Future = field(default_factory=Future)
    done: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None


class AsyncCurlPool:
    """Own a native connection pool, its loop, and each operation until settlement."""
    def __init__(self, *, name="moku-native-network", **session_options):
        self._lock = threading.Lock()
        self._active = 0
        self._retired = False
        self._session_options = session_options
        # libcurl needs socket readers; an owned selector loop avoids a second
        # helper thread on Windows without changing the application's loop policy.
        self._loop = asyncio.SelectorEventLoop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._loop.close()
            raise
        if not self._ready.wait(5):
            self._stop_loop()
            raise RuntimeError("Network event loop did not start")
        initialization = None
        coroutine = None
        try:
            coroutine = self._create_session()
            initialization = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
            self._session = initialization.result(timeout=10)
        except BaseException:
            if initialization is not None:
                initialization.cancel()
            elif coroutine is not None:
                coroutine.close()
            self._stop_loop()
            raise

    def _run(self):
        try:
            asyncio.set_event_loop(self._loop)
            self._ready.set()
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _create_session(self):
        return AsyncSession(**self._session_options)

    def _submit(self, job, operation):
        coroutine = None
        try:
            coroutine = operation(self._session)
            job.task = self._loop.create_task(coroutine)
            job.task.add_done_callback(lambda task: self._settled(job, task))
        except BaseException as error:
            if coroutine is not None:
                coroutine.close()
            job.result.set_exception(error)
            job.done.set()

    @staticmethod
    def _settled(job, task):
        try:
            job.result.set_result(task.result())
        except asyncio.CancelledError:
            job.result.set_exception(ConnectionError("Network operation cancelled"))
        except BaseException as error:
            job.result.set_exception(error)
        finally:
            job.done.set()

    @staticmethod
    def _cancel(job):
        if job.task is not None and not job.task.done():
            job.task.cancel()

    def perform(self, operation, *, check, deadline):
        check()
        job = _AsyncRequest()
        with self._lock:
            if self._retired:
                raise TransportClosedError("Native connection pool retired")
            self._active += 1
        submitted = False
        try:
            self._loop.call_soon_threadsafe(self._submit, job, operation)
            submitted = True
            while True:
                check()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Pixiv response deadline exceeded")
                try:
                    return job.result.result(timeout=.05)
                except FutureTimeoutError:
                    if job.result.done():
                        return job.result.result()
        finally:
            try:
                if submitted and not job.done.is_set():
                    self._loop.call_soon_threadsafe(self._cancel, job)
                    # A Future may report cancellation before its Task's finally
                    # releases native handles. Wait for the Task's actual completion.
                    job.done.wait()
            finally:
                with self._lock:
                    self._active -= 1
                    close = self._retired and self._active == 0
                if close:
                    self._shutdown()

    def _stop_loop(self):
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("Network event loop did not stop")

    def _shutdown(self):
        try:
            asyncio.run_coroutine_threadsafe(self._session.close(), self._loop).result(timeout=5)
        finally:
            self._stop_loop()

    def close(self):
        with self._lock:
            if self._retired:
                return
            self._retired = True
            close = self._active == 0
        if close:
            self._shutdown()


class BrowserTransport(AsyncCurlPool):
    def __init__(self, proxy: str = "", *, profile: BrowserProfile | None = None):
        self.proxy = proxy
        self.configuration = profile if profile is not None else load_browser_profile(history_path().with_name("browser-profile.json"))
        self.profile = self.configuration.name
        super().__init__(
            name="moku-browser-network", impersonate=self.profile, max_clients=8, trust_env=False,
            allow_redirects=False, retry=0,
            extra_fp={"tls_permute_extensions": True},
            curl_options={CurlOpt.SSL_OPTIONS: 16, CurlOpt.MAXCONNECTS: 8},
            curl_infos=[CurlInfo.NUM_CONNECTS],
        )

    async def _transfer(self, session, url, headers, output, maximum, timeout, duration, check, on_chunk=None):
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
            response = await session.get(
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
        return self.perform(lambda session: self._transfer(
            session, url, headers, output, maximum, timeout,
            max(.1, deadline - time.monotonic()), check, on_chunk), check=check, deadline=deadline)
