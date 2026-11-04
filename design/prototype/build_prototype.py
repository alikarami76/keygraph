#!/usr/bin/env python3
"""Bundle the prototype into one offline file: prototype/explorer.html (no external fetches)."""
import pathlib, re, subprocess, sys
P = pathlib.Path(__file__).resolve().parent; D = P.parent
subprocess.run([sys.executable, str(D / "build.py"), "--inline"], check=True)
logo = (D / "logo" / "keygraph-logo-light.svg").read_text()
logo = re.sub(r'<title>.*?</title>', '', logo).replace('#17181A', 'var(--kg-text)').replace('#E2A317', 'var(--kg-accent)')
logo = logo.replace('<svg ', '<svg style="fill:var(--kg-text)" ', 1)
js = lambda p: (p.read_text().replace("</script", "<\\/script"))
mark = (D / "logo" / "keygraph-mark-light.svg").read_text()
mark = re.sub(r'<title>.*?</title>', '', mark).replace('#17181A', 'var(--kg-text)').replace('#E2A317', 'var(--kg-accent)').replace('<svg ', '<svg width="24" height="24" ', 1)
html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>keygraph explorer prototype</title>
<!-- Bundled: Inter and JetBrains Mono (SIL OFL 1.1), @dagrejs/dagre 1.1.4 (MIT). All drawings are keygraph's own. -->
<link rel="icon" href="data:image/svg+xml,{(D / 'logo' / 'favicon.svg').read_text().replace('#', '%23').replace('"', "'")}">
<style>{(D / 'keygraph.inline.css').read_text()}</style></head>
<body><div id="app"></div>
<script>window.KGLOGO = {logo!r}; window.KGMARK = {mark!r};</script>
<script>{js(D / 'vendor' / 'dagre-1.1.4.min.js')}</script>
<script>{js(D / 'kg-theme.js')}</script>
<script>{js(D / 'src' / 'kg-diagram.js')}</script>
<script>{js(P / 'src' / 'data.js')}</script>
<script>{js(P / 'src' / 'app.js')}</script>
</body></html>"""
(P / "explorer.html").write_text(html)
print("wrote", P / "explorer.html", f"{len(html)//1024} KB")
