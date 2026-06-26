#!/usr/bin/env python3
"""Portrait (480x800) mockup. Cards render at FULL natural size (height:auto, no crop,
no overlay), with margins. Uses locally-downloaded covers in images/ so it always loads."""
import json, html, os

fam = json.load(open('fam.json'))['cards']
def ttl(c): return (c.get('card') or {}).get('title') or c.get('cardId')
fam_sorted = sorted(fam, key=lambda c: c.get('lastPlayedAt', ''), reverse=True)
cards = []
for c in fam_sorted:
    cid = c.get('cardId')
    p = f"images/{cid}.png"
    if cid and os.path.exists(p):
        cards.append({'title': ttl(c), 'img': p})

det = json.load(open('card.json'))
dc = det.get('card') or det
chapters = (dc.get('content') or {}).get('chapters') or []
det_title = dc.get('title', 'Treasure Island')
det_img = "images/eDwz6.png"  # Treasure Island
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
  :root{ --green:#1f7a4d; --green2:#155e3a; --coral:#f0502a; --ink:#23303a; --paper:#fbf7f0; }
  *{ box-sizing:border-box; margin:0; padding:0; font-family:'Fredoka',system-ui,sans-serif; }
  body{ background:#e9e3d8; padding:28px; color:var(--ink); }
  .row{ display:flex; gap:34px; align-items:flex-start; flex-wrap:wrap; }
  h1.pagetitle{ font-size:20px; color:#6b6356; font-weight:600; margin:0 0 8px; }
  .cap{ font-size:13px; color:#8a8276; margin:4px 2px 14px; max-width:480px; }
  .col{ width:480px; }
  .frame{ width:480px; height:800px; background:var(--paper); border-radius:30px; overflow:hidden;
          box-shadow:0 18px 40px rgba(0,0,0,.18); display:flex; flex-direction:column; position:relative; }

  .hdr{ height:72px; background:linear-gradient(120deg,var(--green),var(--green2)); color:#fff;
        display:flex; align-items:center; justify-content:space-between; padding:0 22px; flex:0 0 auto; }
  .hdr .who{ font-size:26px; font-weight:700; }
  .pill{ background:rgba(255,255,255,.18); padding:6px 13px; border-radius:999px; font-size:14px;
         display:flex; align-items:center; gap:7px; }
  .dot{ width:9px; height:9px; border-radius:50%; background:#7CFFB0; box-shadow:0 0 8px #7CFFB0; }
  .shelf-label{ font-size:14px; font-weight:600; color:#8a8276; padding:14px 22px 0; flex:0 0 auto; }

  /* FULL CARDS: natural height, no crop, real gaps */
  .grid{ flex:1; min-height:0; overflow-y:auto; padding:16px 18px 18px; display:flex; flex-wrap:wrap;
         gap:18px; align-content:flex-start; justify-content:center; }
  .tile{ width:130px; height:206px; flex:0 0 auto; border-radius:12px; overflow:hidden;
         box-shadow:0 5px 14px rgba(0,0,0,.20); background:#eee; }   /* fixed 130x206 = exact card ratio */
  .tile img{ width:100%; height:100%; object-fit:cover; display:block; }

  .nowbar{ height:66px; flex:0 0 auto; border-top:1px solid #ece5d8; background:#fff;
           display:flex; align-items:center; gap:14px; padding:0 18px; }
  .nowbar .mini{ width:42px; height:42px; border-radius:10px; background:#ffe9e1; display:flex;
                 align-items:center; justify-content:center; color:var(--coral); font-size:20px; }
  .muted{ color:#9a9384; font-size:15px; }

  .back{ position:absolute; top:14px; left:14px; width:42px; height:42px; border-radius:50%;
         background:#fff; box-shadow:0 2px 8px rgba(0,0,0,.18); display:flex; align-items:center;
         justify-content:center; font-size:20px; z-index:5; }
  .dtop{ background:#fff; padding:22px 24px 16px; display:flex; flex-direction:column; align-items:center; flex:0 0 auto; }
  .dcover{ width:150px; height:auto; border-radius:14px; box-shadow:0 10px 24px rgba(0,0,0,.22); }
  .dtitle{ font-size:27px; font-weight:700; color:var(--coral); margin-top:14px; text-align:center; line-height:1.05; }
  .dsub{ font-size:15px; color:#8a8276; margin-top:2px; }
  .playbtn{ width:100%; height:56px; border:none; border-radius:16px; background:var(--coral); color:#fff;
            font-size:22px; font-weight:600; font-family:'Fredoka'; display:flex; align-items:center;
            justify-content:center; gap:10px; margin-top:16px; box-shadow:0 6px 14px rgba(240,80,42,.4); }
  .dsum{ font-size:14px; color:var(--green); font-weight:600; padding:12px 24px 4px; flex:0 0 auto; }
  .chapters{ flex:1; overflow-y:auto; padding:0 24px 8px; }
  .chrow{ display:flex; align-items:center; gap:14px; height:56px; border-bottom:2px dotted #e7e0d2; }
  .chnum{ width:30px; height:30px; border-radius:50%; background:#f1ece0; color:#6b6356;
          display:flex; align-items:center; justify-content:center; font-size:15px; font-weight:600; flex:0 0 auto; }
  .chttl{ flex:1; font-size:18px; }
  .chdur{ color:#9a9384; font-size:15px; }
  .chplay{ color:var(--coral); font-size:14px; width:24px; text-align:center; }

  .np{ background:linear-gradient(165deg,#f0502a,#d83c18); color:#fff; height:100%;
       display:flex; flex-direction:column; align-items:center; padding:20px 30px 30px; }
  .nptop{ width:100%; display:flex; align-items:center; justify-content:center; position:relative; height:30px; }
  .nptop .label{ font-size:16px; font-weight:600; }
  .nptop .chev{ position:absolute; left:0; font-size:22px; }
  .npcover{ width:208px; height:auto; border-radius:18px; margin-top:16px; box-shadow:0 16px 34px rgba(0,0,0,.4); }
  .npt{ font-size:28px; font-weight:700; margin-top:18px; text-align:center; }
  .npc{ font-size:18px; opacity:.9; margin-top:2px; }
  .bar{ width:100%; height:8px; border-radius:8px; background:rgba(255,255,255,.3); margin:18px 0 6px; position:relative; }
  .bar i{ position:absolute; left:0; top:0; bottom:0; width:9%; background:#fff; border-radius:8px; }
  .bar b{ position:absolute; left:9%; top:50%; transform:translate(-50%,-50%); width:18px; height:18px;
          background:#fff; border-radius:50%; box-shadow:0 2px 6px rgba(0,0,0,.3); }
  .times{ width:100%; display:flex; justify-content:space-between; font-size:14px; opacity:.9; }
  .ctrls{ display:flex; align-items:center; justify-content:center; gap:30px; margin-top:18px; }
  .ic{ font-size:32px; opacity:.95; }
  .big{ width:78px; height:78px; border-radius:50%; background:#fff; color:var(--coral);
        display:flex; align-items:center; justify-content:center; font-size:30px; box-shadow:0 6px 16px rgba(0,0,0,.3); }
  .vol{ display:flex; align-items:center; gap:10px; margin-top:18px; font-size:22px; opacity:.95; }
</style></head><body>
<div class="row">
  <div class="col">
    <h1 class="pagetitle">1 &middot; My Cards</h1>
    <div class="cap">Full cards, recently-played first. ~9 whole cards visible, scroll for the rest. Tap a card to open it.</div>
    <div class="frame">
      <div class="hdr"><div class="who">Kiddo&rsquo;s Cards</div><div class="pill"><span class="dot"></span>Kiddo&rsquo;s Yoto</div></div>
      <div class="shelf-label">Keep listening &middot; tap any card</div>
      <div class="grid">__TILES__</div>
      <div class="nowbar"><div class="mini">&#9835;</div><div class="muted">Nothing playing &mdash; tap a card</div></div>
    </div>
  </div>
  <div class="col">
    <h1 class="pagetitle">2 &middot; Detail + chapters</h1>
    <div class="cap">Cover + Play on top, chapter list scrolls below (start from any chapter).</div>
    <div class="frame">
      <div class="back">&#8249;</div>
      <div class="dtop">
        <img class="dcover" src="__DETIMG__">
        <div class="dtitle">__DETTITLE__</div>
        <div class="dsub">Yoto</div>
        <button class="playbtn">&#9654; Play</button>
      </div>
      <div class="dsum">__DETSUM__</div>
      <div class="chapters">__CHROWS__</div>
    </div>
  </div>
  <div class="col">
    <h1 class="pagetitle">3 &middot; Now playing</h1>
    <div class="cap">Big cover, chapter-skip + play/pause, volume (we can cap the max).</div>
    <div class="frame">
      <div class="np">
        <div class="nptop"><span class="chev">&#8964;</span><span class="label">Playing on Kiddo&rsquo;s Yoto</span></div>
        <img class="npcover" src="__DETIMG__">
        <div class="npt">__DETTITLE__</div>
        <div class="npc">Chapter 1</div>
        <div class="bar"><i></i><b></b></div>
        <div class="times"><span>0:42</span><span>__CH1REM__</span></div>
        <div class="ctrls"><span class="ic">&#9198;</span><div class="big">&#10074;&#10074;</div><span class="ic">&#9197;</span></div>
        <div class="vol">&#128264; &#9472;&#9472;&#9472;&#9472;&#9679;&#9472;&#9472; &#128266;</div>
      </div>
    </div>
  </div>
</div>
</body></html>"""

ch1 = int(chapters[0].get('duration') or 478) if chapters else 478
out = (T.replace('__TILES__', tiles).replace('__CHROWS__', chrows)
        .replace('__DETTITLE__', html.escape(det_title)).replace('__DETIMG__', det_img)
        .replace('__DETSUM__', det_summary).replace('__CH1REM__', '-' + mmss(ch1 - 42)))
open('mock_portrait.html', 'w').write(out)
print(f"wrote mock_portrait.html ({len(cards)} full-card tiles, {len(chapters)} chapters)")
