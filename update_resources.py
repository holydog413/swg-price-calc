#!/usr/bin/env python3
"""Fetch current SWG Uprising spawns (Galaxy Harvester galaxy 161) and write resources.json.
Same public endpoint as gh-discord/gh_to_discord.py. Use --push to commit & push to GitHub Pages."""
import json, re, sys, os, subprocess, urllib.request, urllib.parse, datetime
GALAXY = os.environ.get("GH_GALAXY", "161")
STATS = ["OQ","CD","DR","FL","HR","MA","PE","SR","UT","CR","ER"]
HERE = os.path.dirname(os.path.abspath(__file__))
q = {"galaxy": GALAXY, "formatType": "json", "available": "1", "fetchSize": "2000"}
url = "https://galaxyharvester.net/getResources.py?" + urllib.parse.urlencode(q)
raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "dkd-swg-price-calc"}), timeout=120).read().decode("iso-8859-15")
txt = re.sub(r",(\s*[\]}])", r"\1", raw)
try: data = json.loads(txt)
except json.JSONDecodeError:   # GH sometimes omits the closing ] of the resources array
    data = json.loads(re.sub(r"\}(\s*)\}\s*\}\s*$", "}]}}", txt.rstrip()))
res = data["response"].get("resources") or []
out = []
for r in res:
    out.append({"name": r["spawn_name"], "type": r["resource_type_name"], "rt": r["resource_type"], "group": r.get("container_type",""),
        "planets": [p["name"] for p in r.get("planets", []) if "available_by" in p] or [p["name"] for p in r.get("planets", [])],
        "entered": r.get("entered",""), "stats": {s: r[s] for s in STATS if s in r}})
if not out: sys.exit("no resources parsed; not overwriting")
json.dump({"galaxy": int(GALAXY), "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "count": len(out), "resources": out}, open(os.path.join(HERE, "resources.json"), "w"), separators=(",",":"))
print(f"{len(out)} resources written", file=sys.stderr)
if "--push" in sys.argv:
    g = lambda *a: subprocess.run(["git", "-C", HERE, *a], check=True)
    g("add", "resources.json")
    if subprocess.run(["git","-C",HERE,"diff","--cached","--quiet"]).returncode:
        g("commit", "-q", "-m", "Update resources.json"); g("push", "-q")
