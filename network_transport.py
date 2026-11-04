"""Bounded HTTPS reuse and failure classification; route/auth policy stays outside."""
from __future__ import annotations

from dataclasses import dataclass
import errno
import http.client
import io
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request

_GLOBAL_CONNECTION_SLOTS = threading.BoundedSemaphore(8)
_NETWORK_ERRNOS = {errno.ECONNRESET, errno.ECONNABORTED, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ETIMEDOUT, errno.EPIPE}

@dataclass(frozen=True)
class NetworkFailure:
    kind: str
    code: str
    retryable: bool


def classify_network_error(exc: Exception) -> NetworkFailure:
    root = exc
    seen = set()
    while isinstance(root, urllib.error.URLError) and isinstance(root.reason, Exception) and id(root) not in seen:
        seen.add(id(root))
        root = root.reason
    text = str(root).casefold()
    if isinstance(root, urllib.error.HTTPError):
        return NetworkFailure('http', f'HTTP_{root.code}', False)
    if isinstance(root, PermissionError):
        return NetworkFailure('unavailable', 'ACCESS_DENIED', False)
    if isinstance(root, OSError) and not isinstance(root, (ssl.SSLError, ConnectionError, TimeoutError, socket.gaierror)) and not isinstance(exc, urllib.error.URLError):
        retryable = root.errno in _NETWORK_ERRNOS
        return NetworkFailure('unavailable', 'NETWORK_UNAVAILABLE' if retryable else 'LOCAL_IO_ERROR', retryable)
    if isinstance(root, ssl.SSLCertVerificationError) or 'certificate_verify_failed' in text or 'certificate verify failed' in text:
        code = getattr(root, 'verify_code', None)
        return NetworkFailure('certificate', f'CERT_VERIFY_{code}' if type(code) is int else 'CERT_VERIFY_FAILED', False)
    if isinstance(root, TimeoutError) or 'timed out' in text or 'timeout' in text or '超时' in text:
        return NetworkFailure('timeout', 'TIMEOUT', True)
    if isinstance(root, socket.gaierror):
        return NetworkFailure('dns', 'DNS_LOOKUP_FAILED', root.errno == socket.EAI_AGAIN)
    if isinstance(root, ConnectionRefusedError) or 'refused' in text or '拒绝' in text:
        return NetworkFailure('refused', 'CONNECTION_REFUSED', True)
    if isinstance(root, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, http.client.RemoteDisconnected)):
        return NetworkFailure('reset', 'CONNECTION_RESET', True)
    if 'tunnel connection failed' in text:
        return NetworkFailure('proxy', 'PROXY_TUNNEL_FAILED', True)
    if isinstance(root, ssl.SSLError) or 'ssl' in text or 'tls' in text or 'certificate' in text or '证书' in text:
        protocol_errors = ('WRONG_VERSION_NUMBER', 'UNSUPPORTED_PROTOCOL', 'NO_CIPHERS_AVAILABLE', 'TLSV1_ALERT_PROTOCOL_VERSION')
        for code in protocol_errors:
            if code.casefold() in text:
                return NetworkFailure('tls_protocol', code, False)
        reason = getattr(root, 'reason', None)
        if isinstance(reason, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', reason):
            code = reason
        else:
            code = 'TLS_EOF' if isinstance(root, (ssl.SSLEOFError, ssl.SSLZeroReturnError)) or 'eof' in text else 'TLS_HANDSHAKE_FAILED'
        return NetworkFailure('tls', code, True)
    if isinstance(root, http.client.HTTPException):
        return NetworkFailure('reset', 'HTTP_TRANSFER_INTERRUPTED', True)
    retryable = isinstance(exc, urllib.error.URLError) or isinstance(root, ConnectionError) or isinstance(root, OSError) and root.errno in _NETWORK_ERRNOS
    return NetworkFailure('unavailable', 'NETWORK_UNAVAILABLE' if retryable else type(root).__name__, retryable)


class _HTTPSPool:
    def __init__(self, *, maximum=8, per_origin=4, idle_seconds=15.0, context=None):
        self.maximum = maximum
        self.per_origin = per_origin
        self.idle_seconds = idle_seconds
        self._context = context
        self._context_lock = threading.Lock()
        self._condition = threading.Condition()
        self._idle = []
        self._counts = {}
        self._total = 0
        self._closed = False

    def _ssl_context(self):
        with self._context_lock:
            if self._context is None:
                context = ssl.create_default_context()
                context.set_alpn_protocols(['http/1.1'])
                self._context = context
            return self._context

    def _discard(self, key, connection):
        try:
            if connection is not None:
                connection.close()
        finally:
            self._total -= 1
            self._counts[key] -= 1
            if not self._counts[key]:
                del self._counts[key]
            _GLOBAL_CONNECTION_SLOTS.release()

    def acquire(self, request):
        deadline = min(getattr(request, '_moku_deadline', float('inf')), time.monotonic() + request.timeout)
        request._moku_phase = 'tls_context'
        context = self._ssl_context()
        request._moku_phase = 'pool_wait'
        key = (request.host, request._tunnel_host)
        check = getattr(request, '_moku_check', lambda: None)
        with self._condition:
            while True:
                check()
                if self._closed:
                    raise ConnectionError('HTTPS connection pool retired')
                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0:
                    raise TimeoutError('HTTPS connection pool wait timed out')
                while self._idle and now - self._idle[0][0] >= self.idle_seconds:
                    _, old_key, connection = self._idle.pop(0)
                    self._discard(old_key, connection)
                for index in range(len(self._idle) - 1, -1, -1):
                    _, idle_key, connection = self._idle[index]
                    if idle_key == key:
                        self._idle.pop(index)
                        return key, connection, True
                if self._counts.get(key, 0) < self.per_origin:
                    if self._total >= self.maximum and self._idle:
                        _, old_key, connection = self._idle.pop(0)
                        self._discard(old_key, connection)
                    if self._total < self.maximum:
                        slot = _GLOBAL_CONNECTION_SLOTS.acquire(blocking=False)
                        if not slot and self._idle:
                            _, old_key, connection = self._idle.pop(0)
                            self._discard(old_key, connection)
                            slot = _GLOBAL_CONNECTION_SLOTS.acquire(blocking=False)
                        if slot:
                            self._total += 1
                            self._counts[key] = self._counts.get(key, 0) + 1
                            break
                self._condition.wait(min(.05, remaining))
        try:
            connection = http.client.HTTPSConnection(request.host, timeout=request.timeout, context=context)
            return key, connection, False
        except BaseException:
            with self._condition:
                self._discard(key, None)
                self._condition.notify_all()
            raise

    def release(self, key, connection, reusable):
        with self._condition:
            if reusable and not self._closed and connection.sock is not None:
                self._idle.append((time.monotonic(), key, connection))
            else:
                self._discard(key, connection)
            self._condition.notify_all()

    def open(self, request):
        key, connection, reused = self.acquire(request)
        request._moku_reused = reused
        check = getattr(request, '_moku_check', lambda: None)
        response = None
        def set_timeout():
            check()
            remaining = getattr(request, '_moku_deadline', float('inf')) - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('HTTPS request deadline exceeded')
            connection.timeout = min(request.timeout, remaining)
            if connection.sock is not None:
                connection.sock.settimeout(connection.timeout)

        try:
            set_timeout()
            headers = dict(request.unredirected_hdrs)
            headers.update(request.headers)
            headers = {name.title(): value for name, value in headers.items()}
            if request._tunnel_host:
                tunnel_headers = {}
                authorization = headers.pop('Proxy-Authorization', None)
                if authorization is not None:
                    tunnel_headers['Proxy-Authorization'] = authorization
                if not reused:
                    connection.set_tunnel(request._tunnel_host, headers=tunnel_headers)
            # CONNECT response headers use the same cancellable reader. Its
            # close restores the timeout before the following TLS handshake.
            connection.response_class = lambda sock, **options: http.client.HTTPResponse(
                _ResponseSocket(sock, request), **options)
            if connection.sock is None:
                request._moku_phase = 'connect_tls'
                connection.connect()
            set_timeout()
            before_send = getattr(request, '_moku_before_send', None)
            if before_send is not None:
                before_send()
            set_timeout()
            request._moku_phase = 'request'
            connection.request(request.get_method(), request.selector, request.data, headers,
                               encode_chunked=request.has_header('Transfer-encoding'))
            request._moku_phase = 'response_headers'
            set_timeout()
            response = connection.getresponse()
            connection.response_class = http.client.HTTPResponse
            check()
            wrapped = _PooledResponse(response, self, key, connection, request.full_url)
            request._moku_response = wrapped
            return wrapped
        except BaseException:
            try:
                if response is not None:
                    response.close()
            finally:
                self.release(key, connection, False)
            raise

    def close(self):
        with self._condition:
            self._closed = True
            while self._idle:
                _, key, connection = self._idle.pop()
                self._discard(key, connection)
            self._condition.notify_all()


class _ResponseReader(io.RawIOBase):
    """Poll socket reads without poisoning a timed-out buffered makefile.

    The unbuffered owner retains the socket's standard file reference. This
    also keeps connection-close responses readable after HTTPConnection.close.
    """
    def __init__(self, sock, request):
        super().__init__()
        self._sock = sock
        self._timeout = sock.gettimeout()
        self._owner = sock.makefile('rb', buffering=0)
        self._request = request

    def readable(self):
        return True

    def readinto(self, buffer):
        check = getattr(self._request, '_moku_check', lambda: None)
        idle_deadline = time.monotonic() + self._request.timeout
        deadline = min(idle_deadline, getattr(self._request, '_moku_deadline', float('inf')))
        while True:
            check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('HTTPS response read timed out')
            self._sock.settimeout(min(.1, remaining))
            try:
                return self._sock.recv_into(buffer)
            except TimeoutError:
                # Retry the socket, not SocketIO.readinto(), whose timeout flag
                # would permanently invalidate the buffered response stream.
                continue

    def close(self):
        if self.closed:
            return
        try:
            self._sock.settimeout(self._timeout)
        finally:
            try:
                self._owner.close()
            finally:
                super().close()


class _ResponseSocket:
    def __init__(self, sock, request):
        self._sock = sock
        self._request = request

    def makefile(self, mode):
        reader = _ResponseReader(self._sock, self._request)
        try:
            return io.BufferedReader(reader)
        except BaseException:
            reader.close()
            raise


class _PooledResponse:
    def __init__(self, response, pool, key, connection, url):
        self._response = response
        self._pool = pool
        self._key = key
        self._connection = connection
        self._url = url
        self._complete = False
        self._closed = False
        self._lock = threading.Lock()
        self.status = self.code = response.status
        self.msg = response.reason
        self.headers = response.headers

    def __getattr__(self, name):
        return getattr(self._response, name)

    def geturl(self):
        return self._url

    def read(self, count=None):
        data = self._response.read(None if count is None or count < 0 else count)
        self._complete = self._response.isclosed() and self._response.length in (0, None)
        return data

    def _finish(self, normal):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            reusable = normal and self._complete and not self._response.will_close
            closed_normally = False
            try:
                self._response.close()
                closed_normally = True
            finally:
                self._pool.release(self._key, self._connection, reusable and closed_normally)

    def close(self):
        # Explicit close also serves cancellation: only a normal context exit
        # may put a fully consumed connection back in the idle pool.
        self._finish(False)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._finish(exc_type is None)


class _PooledHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pool):
        super().__init__()
        self.pool = pool

    def https_open(self, request):
        try:
            return self.pool.open(request)
        except OSError as exc:
            raise urllib.error.URLError(exc) from exc


class PooledOpener:
    """urllib's policy handlers with a bounded, cancellable HTTPS transport."""
    def __init__(self, proxy_handler, redirect_handler, *, context=None, maximum=8, per_origin=4, idle_seconds=15.0):
        self._pool = _HTTPSPool(maximum=maximum, per_origin=per_origin, idle_seconds=idle_seconds, context=context)
        self._opener = urllib.request.build_opener(proxy_handler, redirect_handler, _PooledHTTPSHandler(self._pool))

    @property
    def handlers(self):
        return self._opener.handlers

    def open(self, request, *, timeout):
        request._moku_response = None
        try:
            return self._opener.open(request, timeout=timeout)
        except BaseException:
            if request._moku_response is not None:
                request._moku_response.close()
            raise

    def close(self):
        self._pool.close()
