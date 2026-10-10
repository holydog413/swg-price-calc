#!/usr/bin/env python3
"""Build schematics.json from Galaxy Harvester's public schematic pages for galaxy 161 (SWG Uprising).
Schematics change rarely: run occasionally (e.g. weekly). Polite: ~1 request/sec, cached in .schem_cache/.
Usage: python3 build_schematics.py [--profs 5,1] [--refresh] [--push]"""
import re, os, sys, json, time, csv, html, subprocess, urllib.request, urllib.parse, datetime
GALAXY = "161"; HERE = os.path.dirname(os.path.abspath(__file__)); UA = {"User-Agent": "dkd-swg-price-calc"}
PROFS = {5:"Architect",1:"Artisan",8:"Chef",16:"Tailor",17:"Weaponsmith",6:"Armorsmith",11:"Droid Engineer",14:"Shipwright",7:"Bio-Engineer",10:"Doctor"}
STATN = {"Overall Quality":"OQ","Conductivity":"CD","Decay Resist":"DR","Flavor":"FL","Heat Resistance":"HR","Malleability":"MA",
         "Potential Energy":"PE","Shock Resistance":"SR","Unit Toughness":"UT","Cold Resistance":"CR","Entangle Resistance":"ER"}
args = sys.argv[1:]
profs = [int(x) for x in args[args.index("--profs")+1].split(",")] if "--profs" in args else list(PROFS)
CACHE = os.path.join(HERE, ".schem_cache"); os.makedirs(CACHE, exist_ok=True)
def get(url, cache=None):
    if cache and "--refresh" not in args and os.path.exists(p := os.path.join(CACHE, cache)): return open(p, encoding="utf-8").read()
    for a in range(3):
        try: s = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace"); break
        except Exception as e: print("retry", url, e, file=sys.stderr); time.sleep(5)
    else: print("SKIP (GH error)", url, file=sys.stderr); return None
    time.sleep(1)
    if cache: open(os.path.join(CACHE, cache), "w", encoding="utf-8").write(s)
    return s
txt = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
schems = {}
for pid in profs:
    lst = get(f"https://galaxyharvester.net/getSchematicList.py?groupType=prof&profession={pid}&galaxy={GALAXY}")
    cat = ""
    for m in re.finditer(r'<h3>(.*?)</h3>|<a href="/schematics.py/([^"]+)">(.*?)</a>', lst):
        if m.group(1): cat = txt(m.group(1)); continue
        sid = m.group(2)
        s = schems.setdefault(sid, {"id": sid, "name": txt(m.group(3)), "profs": [], "cats": []})
        if PROFS.get(pid, str(pid)) not in s["profs"]: s["profs"].append(PROFS.get(pid, str(pid)))
        if cat not in s["cats"]: s["cats"].append(cat)
print(len(schems), "schematics listed", file=sys.stderr)
for n, (sid, s) in enumerate(schems.items()):
    page = get(f"https://galaxyharvester.net/schematics.py/{urllib.parse.quote(sid)}?galaxy={GALAXY}", sid + ".html")
    if page is None: s["error"] = True; continue
    i, j = page.find("<h3>Ingredients"), page.find("ingredient in the following")
    ing = []
    for row in re.findall(r"<tr><td>(\d+)</td>(.*?)(?=<tr>|</table>)", page[i:j], re.S):
        q, rest = int(row[0]), row[1]
        if (r := re.search(r'href="/resourceType.py/([^"]+)">(.*?)</a>', rest)): ing.append({"kind": "res", "id": r.group(1), "name": txt(r.group(2)), "qty": q})
        elif (c := re.search(r'href="([^"]+)">(.*?)</a>', rest)): ing.append({"kind": "comp", "id": c.group(1).split("/")[-1], "name": txt(c.group(2)), "qty": q})
    qi = page.find('id="qualitiesList"'); qs = page[qi:page.find("</ul>", qi)]
    props, w = [], {}
    for blk in re.split(r'<li style="padding-left:10px;font-weight:bold;">', qs)[1:]:
        pname = txt(blk.split("</li>")[0]); sw = {}
        for st, pct in re.findall(r'width:100px;">([^:<]+):\s*</span><span>(\d+)%', blk):
            if st.strip() in STATN: sw[STATN[st.strip()]] = int(pct)
        if sw: props.append({"name": pname, "weights": sw})
        for k, v in sw.items(): w[k] = w.get(k, 0) + v
    tot = sum(w.values()) or 1
    s.update(ingredients=ing, properties=props, weights={k: round(v / tot, 4) for k, v in w.items()})
    if n % 50 == 0: print(n, sid, file=sys.stderr)
# resource group -> member types (GH open-source seed data), so the page can match spawns to slots
SEED = "/workspace/gh-src/database/seedData"; groups = {}
for f in ("typegroup.csv", "typegroupNewPlanet.csv"):
    for t, g in csv.reader(open(os.path.join(SEED, f))): groups.setdefault(g, set()).add(t)
used = {i["id"] for s in schems.values() for i in s.get("ingredients", []) if i["kind"] == "res"}
out = {"galaxy": int(GALAXY), "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "source": "galaxyharvester.net schematics (galaxy 161)", "count": sum(1 for s in schems.values() if not s.get("error")),
       "schematics": sorted((s for s in schems.values() if not s.get("error")), key=lambda s: s["name"]),
       "groups": {g: sorted(groups[g] | {g}) for g in used if g in groups}}
json.dump(out, open(os.path.join(HERE, "schematics.json"), "w"), separators=(",", ":"))
print(len(schems), "schematics written", file=sys.stderr)
if "--push" in args:
    g = lambda *a: subprocess.run(["git", "-C", HERE, *a], check=True)
    g("add", "schematics.json")
    if subprocess.run(["git", "-C", HERE, "diff", "--cached", "--quiet"]).returncode: g("commit", "-q", "-m", "Update schematics.json"); g("push", "-q")
