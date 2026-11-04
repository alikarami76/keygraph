#!/usr/bin/env python3
"""Screenshots of the explorer built from examples/shop (light theme) for the how-it-works PDF.
Build the demo first (examples/shop/build.sh), then: python3 docs/how-it-works/shoot.py"""
import pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
URL = (ROOT / "examples/shop/out/keygraph.html").as_uri()
OUT = pathlib.Path(__file__).resolve().parent / "shots"
VIEWS = {
    "areas": "#areas",
    "area": "#area/a_a66985fc",
    "path": "#path/shop.customers/shop.shipments",
    "table": "#table/shop.orders",
    "column": "#col/shop.orders/status",
    "search": "#search/revenue",
}
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5, color_scheme="light")
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
    pg.goto(URL + "?theme=light")
    pg.evaluate("document.documentElement.setAttribute('data-theme','light')")
    for name, h in VIEWS.items():
        pg.evaluate(f"location.hash = '{h}'")
        pg.wait_for_timeout(900)
        pg.screenshot(path=str(OUT / f"{name}.png"))
    b.close()
print("errors:", errs or "none")
