#!/usr/bin/env python3
"""Pull every card's detail (chapters) via the local proxy (127.0.0.1:8123) and save a
single catalog.json the prototype/firmware can use offline. READ-ONLY (no playback)."""
import json, urllib.request, time

PROXY = "http://127.0.0.1:8123"
fam = json.load(open("fam.json"))["cards"]
cat, fails = [], []
for i, c in enumerate(fam):
    cid = c.get("cardId")
    if not cid:
        continue
    base_card = c.get("card") or {}
    content0 = base_card.get("content") or {}
    meta0 = base_card.get("metadata") or {}
    entry = {
        "cardId": cid,
        "title": base_card.get("title") or cid,
        "img": f"images/{cid}.png",
        "cover": (content0.get("cover") or {}).get("imageL"),
        "description": meta0.get("description"),
        "lastPlayedAt": c.get("lastPlayedAt"),
        "chapters": [],
    }
    try:
        d = json.loads(urllib.request.urlopen(f"{PROXY}/card/{cid}", timeout=20).read())
        card = d.get("card") or d
        for ch in ((card.get("content") or {}).get("chapters") or []):
            entry["chapters"].append({
                "key": ch.get("key"),
                "title": ch.get("title"),
                "duration": ch.get("duration"),
                "tracks": len(ch.get("tracks") or []),
            })
    except Exception as e:
        fails.append((cid, str(e)))
    cat.append(entry)
    if i % 10 == 0:
        print(f"  ...{i+1}/{len(fam)}")
    time.sleep(0.1)

json.dump(cat, open("catalog.json", "w"))
total_ch = sum(len(x["chapters"]) for x in cat)
print(f"wrote catalog.json: {len(cat)} cards, {total_ch} chapters")
if fails:
    print(f"detail fetch failed for {len(fails)}: {fails[:5]}")
