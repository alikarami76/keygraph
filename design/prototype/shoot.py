#!/usr/bin/env python3
"""Screenshot every prototype screen in both themes: prototype/shots/<screen>-<theme>.png (1440x900 @2x) and a 1280 check."""
import pathlib, sys
from playwright.sync_api import sync_playwright
P = pathlib.Path(__file__).resolve().parent; url = (P / "explorer.html").as_uri()
screens = sys.argv[1:] or ["map", "area", "path", "table", "search", "states"]
with sync_playwright() as p:
    b = p.chromium.launch(); errs = []
    for th in ("light", "dark"):
        for w, sc, suf in ((1440, 2, ""), (1280, 1, "-1280")):
            pg = b.new_page(viewport={"width": w, "height": 900}, device_scale_factor=sc, color_scheme=th)
            pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
            for s in screens:
                pg.goto(f"{url}?theme={th}#{s}"); pg.wait_for_timeout(500)
                pg.screenshot(path=str(P / "shots" / f"{s}-{th}{suf}.png"))
            pg.close()
    b.close()
print("errors:", errs or "none")
