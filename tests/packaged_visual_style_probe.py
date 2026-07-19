from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import websocket

from packaged_native_click_login_probe import (
    evaluate,
    free_port,
    launch,
    main_target,
    stop,
    wait_until,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    args = parser.parse_args()
    exe = Path(args.exe).resolve()
    root = Path(tempfile.mkdtemp(prefix="moku-visual-probe-"))
    process = None
    result = {
        "ok": False,
        "folderButton": {},
        "lightRibbons": {},
        "artDepth": {},
        "galleryControls": {},
        "galleryControlsAfterScroll": {},
        "viewport": {},
        "error": "",
    }
    try:
        port = free_port()
        process, base, _ = launch(exe, root, port)
        target = wait_until(lambda: main_target(port, base), 25, "visual probe CDP target")
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        counter = [0]
        try:
            wait_until(
                lambda: evaluate(ws, counter, "document.readyState === 'complete' && !!document.querySelector('#browseFolder')"),
                20,
                "visual probe page",
            )
            visual = evaluate(ws, counter, """(() => {
                const button = getComputedStyle(document.querySelector('#browseFolder'));
                const root = document.querySelector('.light-ribbons');
                const ribbons = [...document.querySelectorAll('.light-ribbon')];
                const depth = document.querySelector('.art-depth');
                const depthStyle = getComputedStyle(depth);
                const bodyStyle = getComputedStyle(document.body);
                const pagerDock = document.querySelector('.pagination-dock');
                const pagerStyle = getComputedStyle(pagerDock);
                const selectAll = document.querySelector('#selectAllPage');
                const clearPage = document.querySelector('#clearPageSelection');
                return {
                    button: {color: button.color, backgroundImage: button.backgroundImage},
                    ribbons: {
                        count: ribbons.length,
                        decorative: root?.getAttribute('aria-hidden') === 'true',
                        pointerEvents: getComputedStyle(root).pointerEvents,
                        animation: getComputedStyle(ribbons[0]).animationName,
                        conservative: document.documentElement.classList.contains('conservative')
                    },
                    artDepth: {
                        moonRings: document.querySelectorAll('.moon-ring').length,
                        moonRingSegments: document.querySelectorAll('.moon-ring').length ? 2 : 0,
                        constellations: document.querySelectorAll('.art-constellation').length,
                        decorative: depth?.getAttribute('aria-hidden') === 'true',
                        pointerEvents: depthStyle.pointerEvents,
                        backgroundLayers: bodyStyle.backgroundImage.split('gradient').length - 1
                    },
                    galleryControls: {
                        selectAllVisible: !!selectAll && getComputedStyle(selectAll).display !== 'none',
                        clearPageVisible: !!clearPage && getComputedStyle(clearPage).display !== 'none',
                        pagerPosition: pagerStyle.position,
                        pagerBottom: pagerStyle.bottom,
                        pagerPointerEvents: pagerStyle.pointerEvents,
                        pagerDisplay: pagerStyle.display
                    },
                    viewport: {
                        width: innerWidth,
                        height: innerHeight,
                        scrollWidth: document.documentElement.scrollWidth,
                        scrollHeight: document.documentElement.scrollHeight,
                        bodyBackgroundImage: bodyStyle.backgroundImage
                    }
                };
            })()""")
            result["folderButton"] = visual["button"]
            result["lightRibbons"] = visual["ribbons"]
            result["artDepth"] = visual["artDepth"]
            result["galleryControls"] = visual["galleryControls"]
            after_scroll = evaluate(ws, counter, """(() => {
                const gallery = document.querySelector('#gallery');
                const dock = document.querySelector('.pagination-dock');
                document.querySelector('#pagination').innerHTML = '<button type="button">1</button>';
                gallery.scrollIntoView({block:'start'});
                updatePaginationDock();
                const style = getComputedStyle(dock);
                return {visible: style.display === 'flex', position: style.position, bottom: style.bottom};
            })()""")
            result["galleryControlsAfterScroll"] = after_scroll
            result["viewport"] = visual["viewport"]
        finally:
            ws.close()
        result["ok"] = (
            result["folderButton"].get("color") == "rgb(10, 17, 26)"
            and "linear-gradient" in result["folderButton"].get("backgroundImage", "")
            and result["lightRibbons"].get("count") == 3
            and result["lightRibbons"].get("decorative")
            and result["lightRibbons"].get("pointerEvents") == "none"
            and result["lightRibbons"].get("animation") == "none"
            and result["lightRibbons"].get("conservative")
            and result["artDepth"].get("moonRings") == 1
            and result["artDepth"].get("moonRingSegments") == 2
            and result["artDepth"].get("constellations") == 2
            and result["artDepth"].get("decorative")
            and result["artDepth"].get("pointerEvents") == "none"
            and result["artDepth"].get("backgroundLayers", 0) >= 2
            and result["galleryControls"].get("selectAllVisible")
            and result["galleryControls"].get("clearPageVisible")
            and result["galleryControls"].get("pagerPosition") == "fixed"
            and result["galleryControls"].get("pagerBottom") == "0px"
            and result["galleryControls"].get("pagerPointerEvents") == "none"
            and result["galleryControls"].get("pagerDisplay") == "none"
            and result["galleryControlsAfterScroll"].get("visible")
            and result["galleryControlsAfterScroll"].get("position") == "fixed"
            and result["galleryControlsAfterScroll"].get("bottom") == "0px"
            and result["viewport"].get("scrollWidth", 0) <= result["viewport"].get("width", 0)
            and result["viewport"].get("scrollHeight", 0) > result["viewport"].get("height", 0)
            and result["viewport"].get("bodyBackgroundImage") != "none"
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop(process, root)
        shutil.rmtree(root, ignore_errors=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
