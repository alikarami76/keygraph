import json, base64, re, math, sys, os
# Regenerates every visual in this folder. Run: python3 design/visuals/gen.py
HERE=os.path.dirname(os.path.abspath(__file__))
D=os.path.dirname(HERE)+"/"
OUT=HERE+"/"
tok=json.load(open(D+"tokens.json"))["color"]
b64=lambda p: base64.b64encode(open(D+p,"rb").read()).decode()
FI=b64("fonts/Inter-latin.var.woff2"); FJ=b64("fonts/JetBrainsMono-latin.var.woff2")

def theme(n):
    c=tok[n]; g=c["graph"]
    return dict(n=n,canvas=c["canvas"],dot=c["canvas-dot"],surface=c["surface"],s2=c["surface-2"],s3=c["surface-3"],
      border=c["border"],bs=c["border-strong"],text=c["text"],t2=c["text-2"],t3=c["text-3"],
      acc=c["accent"],acct=c["accent-text"],accs=c["accent-soft"],
      decl=g["edge-declared"],inf=g["edge-inferred"],con=g["edge-concept"],poly=g["edge-poly"])

def head(t,w,h,extra=""):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" {extra}>
<defs><style>
@font-face{{font-family:'Inter';font-weight:100 900;src:url(data:font/woff2;base64,{FI}) format('woff2');}}
@font-face{{font-family:'JetBrains Mono';font-weight:100 800;src:url(data:font/woff2;base64,{FJ}) format('woff2');}}
.s{{font-family:'Inter',ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif}}
.m{{font-family:'JetBrains Mono',ui-monospace,'SF Mono',Menlo,Consolas,monospace}}
</style>
<pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="12" cy="12" r="1" fill="{t['dot']}"/></pattern></defs>
<rect x=".5" y=".5" width="{w-1}" height="{h-1}" rx="14" fill="{t['canvas']}" stroke="{t['border']}"/>
<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="13" fill="url(#dots)"/>
'''

def logo(t,x,y,h):
    s=open(D+f"logo/keygraph-logo-{t['n']}.svg").read()
    w=h*136.7/32
    s=re.sub(r'<svg [^>]*>',f'<svg x="{x}" y="{y}" width="{w:.2f}" height="{h}" viewBox="0 0 136.7 32">',s,count=1)
    return s

def tri(x,y,col,d=1):  # arrowhead pointing right at x,y
    return f'<path d="M{x} {y} l{-7*d} -3.5 v7 z" fill="{col}"/>'

W=184
def cards(t):
    cs=[("order_items",0,10,[("PK","id","bigint"),("FK","order_id","bigint")]),
        ("payments",0,232,[("PK","id","bigint"),("FK","order_id","bigint")]),
        ("orders",272,115,[("PK","id","bigint"),("FK","user_id","bigint"),("FK","seller_id","bigint")]),
        ("users",544,0,[("PK","id","bigint"),("UK","email","varchar")]),
        ("sellers",544,240,[("PK","id","bigint"),("UK","slug","varchar")])]
    o=[]
    def curve(x1,y1,x2,y2,col,dash,wd,op):
        dx=(x2-x1)*.5; ex=x2-6
        o.append(f'<path d="M{x1} {y1} C{x1+dx} {y1} {ex-dx} {y2} {ex} {y2}" fill="none" stroke="{col}" stroke-width="{wd}" {dash} opacity="{op}" stroke-linecap="round"/>')
        o.append(f'<g opacity="{op}">{tri(x2,y2,col)}<circle cx="{x1}" cy="{y1}" r="2.6" fill="{col}"/></g>')
    curve(184,85,272,164,t['decl'],"",1.6,1)
    curve(456,190,544,49,t['decl'],"",1.6,1)
    curve(456,216,544,289,t['decl'],"",1.6,1)
    curve(184,307,272,164,t['inf'],'stroke-dasharray="5 4"',1.5,.9)
    # chip at bezier midpoint
    mx=(184+272)/2; my=(307+164)/2
    o.append(f'<rect x="{mx-20:.1f}" y="{my-10:.1f}" width="40" height="20" rx="10" fill="{t["surface"]}" stroke="{t["bs"]}"/>'
             f'<text x="{mx:.1f}" y="{my+3.8:.1f}" text-anchor="middle" class="m" font-size="11" font-weight="500" fill="{t["t2"]}">0.92</text>')
    for name,x,y,rows in cs:
        h=36+26*len(rows)+8
        o.append(f'<rect x="{x+.5}" y="{y+.5}" width="{W-1}" height="{h-1}" rx="10" fill="{t["surface"]}" stroke="{t["bs"]}"/>')
        o.append(f'<path d="M{x+.5} {y+36} V{y+10.5} a10 10 0 0 1 10 -10 H{x+W-10.5} a10 10 0 0 1 10 10 V{y+36} z" fill="{t["s2"]}"/>')
        o.append(f'<path d="M{x+1} {y+36.5} H{x+W-1}" stroke="{t["border"]}"/>')
        o.append(f'<rect x="{x+.5}" y="{y+.5}" width="{W-1}" height="{h-1}" rx="10" fill="none" stroke="{t["bs"]}"/>')
        o.append(f'<text x="{x+14}" y="{y+23}" class="m" font-size="13.5" font-weight="600" fill="{t["text"]}">{name}</text>')
        for i,(tag,cn,ty) in enumerate(rows):
            ry=y+36+i*26+13
            pk=tag=="PK"
            o.append(f'<rect x="{x+12}" y="{ry-8}" width="24" height="16" rx="4" fill="{t["accs"] if pk else t["s3"]}"/>'
                     f'<text x="{x+24}" y="{ry+3.4}" text-anchor="middle" class="s" font-size="9" font-weight="650" letter-spacing=".04em" fill="{t["acct"] if pk else t["t2"]}">{tag}</text>')
            o.append(f'<text x="{x+44}" y="{ry+4.2}" class="m" font-size="12" fill="{t["text"]}">{cn}</text>')
            o.append(f'<text x="{x+W-12}" y="{ry+4.2}" text-anchor="end" class="m" font-size="11" fill="{t["t3"]}">{ty}</text>')
    return "\n".join(o)  # local size 728x336

def hero(t):
    s=head(t,1600,480,'aria-label="keygraph: map keys, joins and meaning in any database"')
    s+=logo(t,96,96,44)
    for k,(txt,y) in enumerate([("Map how your tables connect \u2014",232),("for people and AI agents",290)]):
        s+=f'<text class="s" x="96" y="{y}" font-size="44" font-weight="650" letter-spacing="-.012em" fill="{t["text"]}">{txt}</text>'
    s+=f'<text class="s" x="96" y="340" font-size="18" fill="{t["t2"]}">Offline explorer \u00b7 MIT</text>'
    s+=f'<g transform="translate(800 72)">{cards(t)}</g></svg>'
    return s

def social(t):
    w,h=1280,640
    s=head(t,w,h,'aria-label="keygraph"')
    s=s.replace('rx="14"','rx="0"').replace('rx="13"','rx="0"')
    s+=logo(t,100,170,68)
    for txt,y in [("Map how your tables connect \u2014",300),("for people and AI agents",342)]:
        s+=f'<text class="s" x="100" y="{y}" font-size="34" font-weight="650" letter-spacing="-.01em" fill="{t["text"]}">{txt}</text>'
    s+=f'<text class="s" x="100" y="386" font-size="17" fill="{t["t2"]}">Offline explorer \u00b7 MIT</text>'
    s+=f'<text class="m" x="100" y="520" font-size="15" fill="{t["t3"]}">github.com/alikarami76/keygraph</text>'
    s+=f'<g transform="translate(668 178) scale(.74)">{cards(t)}</g></svg>'
    return s

# ---------- how it works
def icon(t,k,x,y):
    st=f'fill="none" stroke="{t["text"]}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
    ac=f'fill="none" stroke="{t["acc"]}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
    if k==1:
        b=f'<path {st} d="M7 8v16c0 1.9 4 3.5 9 3.5s9-1.6 9-3.5V8"/><path {st} d="M7 16c0 1.9 4 3.5 9 3.5s9-1.6 9-3.5"/><ellipse {ac} cx="16" cy="8" rx="9" ry="3.5"/>'
    elif k==2:
        b=(f'<path {st} d="M11.4 16H17l4.2-5M17 16l4.2 5"/><circle {st} cx="8" cy="16" r="3.4"/><circle {st} cx="24" cy="9" r="2.8"/>'
           f'<circle {ac} cx="24" cy="23" r="2.8"/>')
    elif k==3:
        b=(f'<path {st} d="M5 16l11 6 11-6M5 21l11 6 11-6"/><path {ac} d="M16 5l11 6-11 6-11-6z"/>')
    else:
        b=(f'<rect {st} x="4" y="6" width="24" height="20" rx="3"/><path {st} d="M4 12h24M9 18h9M9 22h6"/><circle cx="8" cy="9" r="1.2" fill="{t["acc"]}" stroke="none"/>')
    return f'<g transform="translate({x} {y})">{b}</g>'

STAGES=[
 ("Sources",1,[("Live database scan","read-only, metadata only",0),("information_schema dumps","for when you cannot connect",1),("Context files","YAML or markdown notes",0)]),
 ("Keys & relations",2,[("Primary and unique keys","with indexes in column order",0),("Declared foreign keys","read straight from the schema",0),("Inferred relations","each with a confidence and evidence",0),("Hubs","tables that many others point to",0)]),
 ("Context & profiles",3,[("Areas","tables grouped by business domain",0),("Concepts and synonyms","so “customer” finds users",0),("Column meanings","enum codes such as 3 = shipped",0),("Value profiles","top values, nulls and ranges",0)]),
 ("Outputs",4,[("keygraph.html","offline explorer in one file",1),("graph.json + keygraph.db","SQLite full-text search",1),("CLI","path, describe, area, search",0),("Markdown + Mermaid","for AI agents",0)]),
]
def how(t):
    w,h=1600,680
    s=head(t,w,h,'aria-label="How keygraph works: sources, keys and relations, context and profiles, outputs"')
    s+=f'<text class="s" x="56" y="84" font-size="28" font-weight="650" letter-spacing="-.02em" fill="{t["text"]}">How keygraph works</text>'
    s+=f'<text class="s" x="56" y="114" font-size="16" fill="{t["t2"]}">From a database to a map your team and your AI agents can use.</text>'
    cw,gap,cy,ch=330,60,150,400
    for i,(title,ic,items) in enumerate(STAGES):
        x=56+i*(cw+gap)
        s+=f'<rect x="{x+.5}" y="{cy+.5}" width="{cw-1}" height="{ch-1}" rx="10" fill="{t["surface"]}" stroke="{t["bs"]}"/>'
        s+=f'<rect x="{x+28.5}" y="{cy+28.5}" width="51" height="51" rx="10" fill="{t["s2"]}" stroke="{t["border"]}"/>'
        s+=icon(t,ic,x+38,cy+38)
        s+=f'<text class="s" x="{x+96}" y="{cy+59}" font-size="11" font-weight="600" letter-spacing=".08em" fill="{t["t3"]}">STAGE {i+1}</text>'
        s+=f'<text class="s" x="{x+28}" y="{cy+124}" font-size="22" font-weight="650" letter-spacing="-.015em" fill="{t["text"]}">{title.replace("&","&amp;")}</text>'
        for j,(a,b,mono) in enumerate(items):
            yy=cy+168+j*62
            s+=f'<rect x="{x+28}" y="{yy-14}" width="2" height="38" rx="1" fill="{t["bs"]}"/>'
            fam='m' if mono else 's'; fs=14 if mono else 15.5
            s+=f'<text class="{fam}" x="{x+44}" y="{yy+3}" font-size="{fs}" font-weight="{550 if not mono else 500}" fill="{t["text"]}">{a}</text>'
            bm = (i==3 and j==2)
            s+=f'<text class="{"m" if bm else "s"}" x="{x+44}" y="{yy+22}" font-size="{12.5 if bm else 13.5}" fill="{t["t3"]}">{b}</text>'
        if i<3:
            ax=x+cw+10; ay=cy+54
            s+=f'<path d="M{ax} {ay} H{ax+34} M{ax+28} {ay-5} L{ax+37} {ay} L{ax+28} {ay+5}" fill="none" stroke="{t["t3"]}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
    s+=f'<text class="s" x="56" y="612" font-size="14" fill="{t["t2"]}">keygraph never writes to your database: it reads metadata, and samples values only when you run a profile. Everything it produces stays on your machine.</text>'
    return s+'</svg>'

# ---------- legend
def legend(t):
    w,h=900,360
    s=head(t,w,h,'aria-label="Relation legend: declared, inferred, concept, polymorphic, hub label"')
    s+=f'<text class="s" x="40" y="46" font-size="11" font-weight="600" letter-spacing=".08em" fill="{t["t3"]}">HOW TO READ A RELATION</text>'
    def line(x,y,col,dash,wd,op,arrow=True,src=None,L=100):
        r=f'<path d="M{x} {y} H{x+L-(6 if arrow else 0)}" stroke="{col}" stroke-width="{wd}" {dash} opacity="{op}" stroke-linecap="round"/>'
        r+=f'<circle cx="{x}" cy="{y}" r="2.6" fill="{col}" opacity="{op}"/>' if src is None else ''
        if arrow: r+=f'<g opacity="{op}">{tri(x+L,y,col)}</g>'
        if src=="diamond": r+=f'<path d="M{x} {y-5} l5 5 l-5 5 l-5 -5z" fill="{col}"/>'
        return r
    rows=[("Declared","Foreign key written in the schema.")]
    y0=100; rh=52
    for k in range(5):
        y=y0+k*rh
        if k>0: s+=f'<path d="M40 {y-26} H860" stroke="{t["border"]}"/>'
    def lab(k,text): return f'<text class="s" x="40" y="{y0+k*rh+5}" font-size="15" font-weight="600" fill="{t["text"]}">{text}</text>'
    def desc(k,text): return f'<text class="s" x="640" y="{y0+k*rh+5}" font-size="13" fill="{t["t2"]}">{text}</text>'
    s+=lab(0,"Declared")+line(190,y0,t["decl"],"",1.6,1)+desc(0,"A foreign key in the schema.")
    s+=lab(1,"Inferred")
    for n,(cf,wd,op) in enumerate([("0.95",1.8,.9),("0.75",1.4,.62),("0.50",1.0,.35)]):
        x=190+n*140; y=y0+rh
        s+=line(x,y,t["inf"],'stroke-dasharray="5 4"',wd,op,L=84)
        s+=f'<rect x="{x+90}" y="{y-10}" width="40" height="20" rx="10" fill="{t["surface"]}" stroke="{t["bs"]}"/><text x="{x+110}" y="{y+3.8}" text-anchor="middle" class="m" font-size="11" font-weight="500" fill="{t["t2"]}">{cf}</text>'
    s+=desc(1,"From names. Fainter = less sure.")
    y=y0+2*rh
    s+=lab(2,"Concept")+f'<path d="M190 {y} H290" stroke="{t["con"]}" stroke-width="1.4" stroke-dasharray="0.1 4.2" stroke-linecap="round"/><circle cx="190" cy="{y}" r="2.6" fill="{t["con"]}"/><circle cx="290" cy="{y}" r="2.6" fill="{t["con"]}"/>'+desc(2,"Links a table to a business term.")
    y=y0+3*rh
    s+=lab(3,"Polymorphic")+line(190,y,t["poly"],'stroke-dasharray="1 3 6 3"',1.4,1,src="diamond")+desc(3,"One column, many tables.")
    y=y0+4*rh
    s+=lab(4,"Hub label")+f'<rect x="190" y="{y-12}" width="76" height="24" rx="12" fill="{t["s3"]}" stroke="{t["bs"]}"/>'
    s+=f'<path d="M203 {y} h10 m-4 -3.6 l4 3.6 l-4 3.6" fill="none" stroke="{t["t2"]}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><text class="m" x="222" y="{y+4}" font-size="11.5" fill="{t["text"]}">users</text>'
    s+=desc(4,"The table many keys point to.")
    return s+'</svg>'


# ---------- section banners (1600x200, one family)
def mc(t,x,y,name,rows,w=190):
    h=30+28*len(rows)+6; o=[]
    o.append(f'<rect x="{x+.5}" y="{y+.5}" width="{w-1}" height="{h-1}" rx="10" fill="{t["surface"]}" stroke="{t["bs"]}"/>')
    o.append(f'<path d="M{x+.5} {y+30} V{y+10.5} a10 10 0 0 1 10 -10 H{x+w-10.5} a10 10 0 0 1 10 10 V{y+30} z" fill="{t["s2"]}"/>')
    o.append(f'<path d="M{x+1} {y+30.5} H{x+w-1}" stroke="{t["border"]}"/>')
    o.append(f'<rect x="{x+.5}" y="{y+.5}" width="{w-1}" height="{h-1}" rx="10" fill="none" stroke="{t["bs"]}"/>')
    o.append(f'<text x="{x+14}" y="{y+20}" class="m" font-size="13.5" font-weight="600" fill="{t["text"]}">{name}</text>')
    for i,(tag,cn) in enumerate(rows):
        ry=y+30+i*28+14; pk=tag=="PK"
        o.append(f'<rect x="{x+12}" y="{ry-8}" width="24" height="16" rx="4" fill="{t["accs"] if pk else t["s3"]}"/>'
                 f'<text x="{x+24}" y="{ry+3.4}" text-anchor="middle" class="s" font-size="9" font-weight="650" letter-spacing=".04em" fill="{t["acct"] if pk else t["t2"]}">{tag}</text>'
                 f'<text x="{x+46}" y="{ry+4.4}" class="m" font-size="12.5" fill="{t["text"]}">{cn}</text>')
    return "".join(o)
def edge(x1,y1,x2,y2,col,dash="",wd=1.6,op=1,arrow=True):
    dx=(x2-x1)*.5; ex=x2-(6 if arrow else 0)
    r=f'<path d="M{x1} {y1} C{x1+dx} {y1} {ex-dx} {y2} {ex} {y2}" fill="none" stroke="{col}" stroke-width="{wd}" {dash} opacity="{op}" stroke-linecap="round"/>'
    r+=f'<g opacity="{op}">{tri(x2,y2,col) if arrow else ""}<circle cx="{x1}" cy="{y1}" r="2.6" fill="{col}"/></g>'
    return r
def chip(t,cx,cy,txt):
    return (f'<rect x="{cx-20}" y="{cy-10}" width="40" height="20" rx="10" fill="{t["surface"]}" stroke="{t["bs"]}"/>'
            f'<text x="{cx}" y="{cy+3.8}" text-anchor="middle" class="m" font-size="11" font-weight="500" fill="{t["t2"]}">{txt}</text>')
X0=1010
def motif(t,k):
    if k=="quickstart":
        return mc(t,X0,20,"orders",[("PK","id"),("FK","user_id")])+mc(t,X0+300,88,"users",[("PK","id"),("UK","email")])+edge(X0+190,20+30+28+14,X0+300,88+44,t["decl"])
    if k=="explorer-views":
        o=f'<rect x="{X0+.5}" y="24.5" width="494" height="151" rx="10" fill="{t["surface"]}" stroke="{t["bs"]}"/>'
        o+=f'<path d="M{X0+.5} 58 V34.5 a10 10 0 0 1 10 -10 H{X0+483.5} a10 10 0 0 1 10 10 V58 z" fill="{t["s2"]}"/><path d="M{X0+1} 58.5 H{X0+493}" stroke="{t["border"]}"/>'
        for i,w in enumerate([58,74,52,64]):
            x=X0+20+sum([58,74,52,64][:i])+i*22
            o+=f'<rect x="{x}" y="38" width="{w}" height="7" rx="3.5" fill="{t["bs"]}"/>'
            if i==1: o+=f'<rect x="{x}" y="55" width="{w}" height="3" rx="1.5" fill="{t["acc"]}"/>'
        nodes=[(X0+40,86),(X0+200,118),(X0+350,84)]
        o+=edge(nodes[0][0]+80,nodes[0][1]+16,nodes[1][0],nodes[1][1]+16,t["decl"])+edge(nodes[1][0]+80,nodes[1][1]+16,nodes[2][0],nodes[2][1]+16,t["decl"])
        for (x,y) in nodes:
            o+=f'<rect x="{x+.5}" y="{y+.5}" width="79" height="31" rx="7" fill="{t["surface"]}" stroke="{t["bs"]}"/><rect x="{x+12}" y="{y+11}" width="40" height="7" rx="3.5" fill="{t["t3"]}" opacity=".6"/><rect x="{x+12}" y="{y+21}" width="26" height="4" rx="2" fill="{t["bs"]}"/>'
        return o
    if k=="ai-agents":
        dx=X0+300
        o=mc(t,X0,50,"orders",[("PK","id"),("FK","user_id")])+edge(X0+190,50+44+28,dx,100,t["decl"])
        o+=f'<rect x="{dx+.5}" y="24.5" width="149" height="151" rx="10" fill="{t["surface"]}" stroke="{t["bs"]}"/>'
        for i,w in enumerate([70,100,84,110,60,92]):
            o+=f'<rect x="{dx+20}" y="{50+i*18}" width="{w}" height="6" rx="3" fill="{t["acc"] if i==0 else t["bs"]}"/>'
        return o
    if k=="relations-inferred":
        o=mc(t,X0,20,"orders",[("PK","id"),("FK","user_id")])+mc(t,X0+300,88,"users",[("PK","id"),("UK","email")])
        o+=edge(X0+190,20+30+28+14,X0+300,88+44,t["inf"],'stroke-dasharray="5 4"',1.5,.9)
        o+=chip(t,X0+245,(92+132)/2,"0.92")
        return o
    if k=="faq":
        bx=X0+300
        o=mc(t,X0,40,"orders",[("PK","id"),("FK","user_id")])
        o+=f'<path d="M{X0+190} 98 H{bx}" stroke="{t["con"]}" stroke-width="1.4" stroke-dasharray="0.1 4.2" stroke-linecap="round"/><circle cx="{X0+190}" cy="98" r="2.6" fill="{t["con"]}"/>'
        o+=f'<path d="M{bx+10} 36 H{bx+124} a10 10 0 0 1 10 10 V138 a10 10 0 0 1 -10 10 H{bx+40} l-18 16 v-16 H{bx+10} a10 10 0 0 1 -10 -10 V46 a10 10 0 0 1 10 -10z" transform="translate(0 0)" fill="{t["surface"]}" stroke="{t["bs"]}"/>'
        o+=f'<text x="{bx+67}" y="114" text-anchor="middle" class="s" font-size="64" font-weight="600" fill="{t["acc"]}">?</text>'
        return o
BAN=[("quickstart","Quickstart"),("explorer-views","Explorer views"),("ai-agents","Use with AI agents"),("relations-inferred","How relations are inferred"),("faq","FAQ")]
def banner(t,k,title):
    s=head(t,1600,200,f'aria-label="{title}"')
    s+=f'<text class="s" x="96" y="121" font-size="60" font-weight="600" letter-spacing="-.02em" fill="{t["text"]}">{title}</text>'
    return s+motif(t,k)+'</svg>'

for n in ("light","dark"):
    t=theme(n)
    open(OUT+f"readme-hero-{n}.svg","w").write(hero(t))
    open(OUT+f"how-it-works-{n}.svg","w").write(how(t))
    open(OUT+f"relations-legend-{n}.svg","w").write(legend(t))
    for k,title in BAN: open(OUT+f"section-{k}-{n}.svg","w").write(banner(t,k,title))
SOC=OUT+"social-preview.svg"; open(SOC,"w").write(social(theme("dark")))

from playwright.sync_api import sync_playwright
jobs=[(f"section-{k}-{n}",1600,200,2) for k,_ in BAN for n in("light","dark")]+[(f"readme-hero-{n}",1600,480,2) for n in("light","dark")]+[(f"how-it-works-{n}",1600,680,2) for n in("light","dark")]+[(f"relations-legend-{n}",900,360,2) for n in("light","dark")]+[("social-preview",1280,640,1)]
with sync_playwright() as p:
    b=p.chromium.launch()
    for name,w,h,sc in jobs:
        pg=b.new_page(viewport={"width":w,"height":h},device_scale_factor=sc)
        pg.goto("file://"+OUT+name+".svg"); pg.wait_for_timeout(400)
        pg.evaluate("document.fonts.ready")
        pg.screenshot(path=OUT+name+".png",omit_background=(name!="social-preview"))
        pg.close()
    b.close()
os.remove(SOC)  # social preview is shipped as PNG only
