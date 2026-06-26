#!/usr/bin/env python3
"""Landscape (800x480) mockup. Full cards (fixed-size tiles, no crop), local images.
Detail uses the landscape split (cover+Play left, chapters right)."""
import json, html, os

fam = json.load(open('fam.json'))['cards']
def ttl(c): return (c.get('card') or {}).get('title') or c.get('cardId')
fam_sorted = sorted(fam, key=lambda c: c.get('lastPlayedAt', ''), reverse=True)
cards = [{'title': ttl(c), 'img': f"images/{c.get('cardId')}.png"} for c in fam_sorted
         if c.get('cardId') and os.path.exists(f"images/{c.get('cardId')}.png")]

det = json.load(open('card.json'))
dc = det.get('card') or det
chapters = (dc.get('content') or {}).get('chapters') or []
det_title = dc.get('title', 'Treasure Island')
det_img = "images/eDwz6.png"
def mmss(s):
    s = int(s or 0); return f"{s//60}:{s%60:02d}"
total = sum(int(ch.get('duration') or 0) for ch in chapters)
det_summary = f"{len(chapters)} chapters &middot; {total//3600}h {(total%3600)//60}m"

tiles = "\n".join(f'<div class="tile"><img src="{c["img"]}"></div>' for c in cards)
chrows = "\n".join(
    f'<div class="chrow"><div class="chnum">{i+1}</div>'
    f'<div class="chttl">{html.escape(ch.get("title") or ("Chapter "+str(i+1)))}</div>'
    f'<div class="chdur">{mmss(ch.get("duration"))}</div><div class="chplay">&#9654;</div></div>'
    for i, ch in enumerate(chapters))

T = r"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Fredoka:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{ --red:#e5352b; --red-deep:#b3211a; --sun:#ffb02e; --ink:#23303a; --paper:#fbf7f0; }
  *{ box-sizing:border-box; margin:0; padding:0; font-family:'Fredoka',system-ui,sans-serif; }
  body{ background:#e9e3d8; padding:28px; color:var(--ink); }
  h1.pagetitle{ font-size:20px; color:#6b6356; font-weight:600; margin:26px 0 8px; }
  .cap{ font-size:13px; color:#8a8276; margin:4px 2px 14px; max-width:800px; }
  .frame{ width:800px; height:480px; background:var(--paper); border-radius:26px; overflow:hidden;
          box-shadow:0 18px 40px rgba(0,0,0,.18); display:flex; flex-direction:column; position:relative; }

  .hdr{ height:60px; background:linear-gradient(120deg,var(--green),var(--green2)); color:#fff;
        display:flex; align-items:center; justify-content:space-between; padding:0 22px; flex:0 0 auto; }
  .hdr .who{ font-size:24px; font-weight:700; }
  .pill{ background:rgba(255,255,255,.18); padding:6px 13px; border-radius:999px; font-size:14px;
         display:flex; align-items:center; gap:7px; }
  .dot{ width:9px; height:9px; border-radius:50%; background:#7CFFB0; box-shadow:0 0 8px #7CFFB0; }
  .grid{ flex:1; min-height:0; overflow-y:auto; padding:14px 18px; display:flex; flex-wrap:wrap;
         gap:16px; align-content:flex-start; justify-content:center; }
  .tile{ width:122px; height:193px; flex:0 0 auto; border-radius:11px; overflow:hidden;
         box-shadow:0 4px 12px rgba(0,0,0,.20); background:#eee; }
  .tile img{ width:100%; height:100%; object-fit:cover; display:block; }
  .nowbar{ height:56px; flex:0 0 auto; border-top:1px solid #ece5d8; background:#fff;
           display:flex; align-items:center; gap:14px; padding:0 18px; }
  .nowbar .mini{ width:38px; height:38px; border-radius:9px; background:#ffe9e1; display:flex;
                 align-items:center; justify-content:center; color:var(--red); font-size:18px; background:#ffe1de !important; }
  .muted{ color:#9a9384; font-size:15px; }
  .status{ display:flex; align-items:center; gap:8px; font-size:14px; font-weight:600; color:#6b6356;
           background:#f1ece0; padding:7px 13px; border-radius:999px; flex:0 0 auto; }

  /* detail: landscape split */
  .back{ position:absolute; top:13px; left:13px; width:40px; height:40px; border-radius:50%;
         background:#fff; box-shadow:0 2px 8px rgba(0,0,0,.18); display:flex; align-items:center;
         justify-content:center; font-size:19px; z-index:5; }
  .d{ flex:1; min-height:0; display:flex; }
  .dleft{ width:286px; flex:0 0 auto; padding:24px 20px; display:flex; flex-direction:column;
          align-items:center; gap:16px; background:#fff; }
  .dcover{ width:184px; height:auto; border-radius:14px; box-shadow:0 10px 24px rgba(0,0,0,.22); }
  .playbtn{ width:100%; height:54px; border:none; border-radius:15px; background:var(--red); color:#fff;
            font-size:21px; font-weight:600; font-family:'Fredoka'; display:flex; align-items:center;
            justify-content:center; gap:9px; box-shadow:0 6px 14px rgba(229,53,43,.45); }
  .dright{ flex:1; min-width:0; padding:22px 24px 8px 10px; display:flex; flex-direction:column; }
  .dtitle{ font-size:28px; font-weight:700; color:var(--red); line-height:1.04; }
  .dsub{ font-size:15px; color:#8a8276; margin:3px 0; }
  .dsum{ font-size:14px; color:var(--green); font-weight:600; margin:8px 0 4px; }
  .chapters{ flex:1; min-height:0; overflow-y:auto; padding-right:6px; }
  .chrow{ display:flex; align-items:center; gap:13px; height:50px; border-bottom:2px dotted #e7e0d2; }
  .chnum{ width:28px; height:28px; border-radius:50%; background:var(--sun); color:#6a4400;
          display:flex; align-items:center; justify-content:center; font-size:14px; font-weight:600; flex:0 0 auto; }
  .chttl{ flex:1; font-size:17px; }
  .chdur{ color:#9a9384; font-size:14px; }
  .chplay{ color:var(--red); font-size:13px; width:22px; text-align:center; }

  /* now playing: landscape */
  .np{ background:linear-gradient(150deg,var(--red),var(--red-deep)); color:#fff; height:100%;
       display:flex; flex-direction:column; padding:14px 30px; }
  .nptop{ display:flex; align-items:center; justify-content:center; position:relative; height:26px; }
  .nptop .label{ font-size:15px; font-weight:600; }
  .nptop .chev{ position:absolute; left:0; font-size:20px; }
  .npmid{ flex:1; min-height:0; display:flex; align-items:center; gap:30px; }
  .npcover{ width:150px; height:auto; border-radius:14px; box-shadow:0 12px 28px rgba(0,0,0,.4); flex:0 0 auto; }
  .npinfo{ flex:1; min-width:0; }
  .npinfo .t{ font-size:28px; font-weight:700; }
  .npinfo .c{ font-size:18px; opacity:.9; margin-top:2px; }
  .bar{ height:8px; border-radius:8px; background:rgba(255,255,255,.3); margin:18px 0 6px; position:relative; }
  .bar i{ position:absolute; left:0; top:0; bottom:0; width:9%; background:#fff; border-radius:8px; }
  .bar b{ position:absolute; left:9%; top:50%; transform:translate(-50%,-50%); width:18px; height:18px;
          background:#fff; border-radius:50%; box-shadow:0 2px 6px rgba(0,0,0,.3); }
  .times{ display:flex; justify-content:space-between; font-size:13px; opacity:.9; }
  .ctrls{ display:flex; align-items:center; gap:26px; margin-top:14px; }
  .ic{ font-size:28px; opacity:.95; }
  .big{ width:66px; height:66px; border-radius:50%; background:#fff; color:var(--red);
        display:flex; align-items:center; justify-content:center; font-size:27px; box-shadow:0 6px 16px rgba(0,0,0,.3); }
  .vol{ margin-left:auto; font-size:20px; opacity:.95; }
</style></head><body>

<h1 class="pagetitle">LANDSCAPE &middot; 1 &middot; My Cards</h1>
<div class="cap">Full cards (122px), ~5 per row, ~1.5 rows before scrolling. Wider but shorter than portrait, so fewer rows show.</div>
<div class="frame">
  <div class="grid">__TILES__</div>
  <div class="nowbar">
    <div class="status"><span class="dot"></span>Kiddo&rsquo;s Yoto</div>
    <div class="mini">&#9835;</div><div class="muted">Nothing playing &mdash; tap a card to start</div>
  </div>
</div>

<h1 class="pagetitle">LANDSCAPE &middot; 2 &middot; Detail + chapters</h1>
<div class="cap">Landscape's advantage: cover + Play on the left, the full chapter list on the right at the same time.</div>
<div class="frame">
  <div class="back">&#8249;</div>
  <div class="d">
    <div class="dleft"><img class="dcover" src="__DETIMG__"><button class="playbtn">&#9654; Play</button></div>
    <div class="dright">
      <div class="dtitle">__DETTITLE__</div><div class="dsub">Yoto</div>
      <div class="dsum">__DETSUM__</div>
      <div class="chapters">__CHROWS__</div>
    </div>
  </div>
  <div class="nowbar"><div class="status"><span class="dot"></span>Kiddo&rsquo;s Yoto</div><div class="mini">&#9835;</div><div class="muted">Nothing playing</div></div>
</div>

<h1 class="pagetitle">LANDSCAPE &middot; 3 &middot; Now playing</h1>
<div class="cap">Cover left, controls right.</div>
<div class="frame">
  <div class="np">
    <div class="nptop"><span class="chev">&#8964;</span><span class="label">Playing on Kiddo&rsquo;s Yoto</span></div>
    <div class="npmid">
      <img class="npcover" src="__DETIMG__">
      <div class="npinfo">
        <div class="t">__DETTITLE__</div><div class="c">Chapter 1</div>
        <div class="bar"><i></i><b></b></div>
        <div class="times"><span>0:42</span><span>__CH1REM__</span></div>
        <div class="ctrls"><span class="ic">&#9198;</span><div class="big">&#10074;&#10074;</div><span class="ic">&#9197;</span>
          <span class="vol">&#128264; &#9472;&#9472;&#9472;&#9679;&#9472;&#9472; &#128266;</span></div>
      </div>
    </div>
  </div>
</div>
</body></html>"""

ch1 = int(chapters[0].get('duration') or 478) if chapters else 478
out = (T.replace('__TILES__', tiles).replace('__CHROWS__', chrows)
        .replace('__DETTITLE__', html.escape(det_title)).replace('__DETIMG__', det_img)
        .replace('__DETSUM__', det_summary).replace('__CH1REM__', '-' + mmss(ch1 - 42)))
open('mock.html', 'w').write(out)
print(f"wrote mock.html ({len(cards)} tiles, {len(chapters)} chapters)")
