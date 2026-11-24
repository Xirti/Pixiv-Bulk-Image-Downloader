"""Opt-in local diagnostics for one artwork across the list and detail readers."""
import logging
import os

from pixiv_adapter import valid_pixiv_id


TRACE_PID = os.environ.get("MOKU_TRACE_PIXIV_PID", "").strip()
if not valid_pixiv_id(TRACE_PID):
    TRACE_PID = ""
_log = logging.getLogger("moku.http")


def trace_artwork_rows(source, stage, rows, *, mode, scope="", position=None):
    if not TRACE_PID:
        return
    found = any(str(row.get("id")) == TRACE_PID if isinstance(row, dict) else row == TRACE_PID for row in rows)
    _log.info("pixiv-read pid=%s source=%s stage=%s present=%s mode=%s scope=%s position=%s",
              TRACE_PID, source, stage, found, mode, scope, position)
