#!/usr/bin/env python3
"""Ingest SWG in-game inventory commands (relayed to Discord by 'Uprising Bot') into inventory.json.

Input (file args or stdin), any of:
  * JSON array or JSON lines of {"id": "<discord message id>", "author": "Uprising Bot",
    "content": "rover: inv +200k Test @5", "timestamp": "2026-10-10T10:54:00Z"}
  * tab-separated text lines: <id>\t<timestamp>\t<author>\t<content>
Only author "Uprising Bot" with content starting "rover: inv" is processed; message ids are
deduped via inv_seen.json. Commands:
  inv +<qty> <name> [@<cpu>]   add stock (purchase CPU averaged by quantity)
  inv -<qty> <name>            remove stock
  inv sold <qty> <name> @<px>  record a sale (on hand -= qty, qty sold += qty at px)
  inv check <name>             no-op
Quantities: 200000, 200k, 1.5m.  Options: --push (commit+push inventory.json), --dry-run.
"""
import json, re, os, sys, subprocess, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
INV, SEEN = os.path.join(HERE, "inventory.json"), os.path.join(HERE, "inv_seen.json")
BOT, PREFIX = "uprising bot", "rover: inv"
args = [a for a in sys.argv[1:] if not a.startswith("--")]; DRY = "--dry-run" in sys.argv

def qty(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([km]?)", s.lower().replace(",", ""))
    if not m: raise ValueError(f"bad quantity {s!r}")
    return round(float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2)])
def num(s): return float(qty(s)) if re.fullmatch(r"\d+(\.\d+)?[km]", s.lower()) else float(s.replace(",", ""))

CMD = re.compile(r"^rover:\s*inv\s+(?:(?P<op>[+-])\s*(?P<q>[\d.,]+[kKmM]?)\s+(?P<name>.+?)(?:\s+@\s*(?P<px>[\d.,]+[kKmM]?))?"
                 r"|sold\s+(?P<sq>[\d.,]+[kKmM]?)\s+(?P<sname>.+?)\s+@\s*(?P<spx>[\d.,]+[kKmM]?)|check\s+(?P<cname>.+?))\s*$", re.I)
def parse(content):
    m = CMD.match(content.strip())
    if not m: return None
    if m["op"]: return {"op": "add" if m["op"] == "+" else "remove", "qty": qty(m["q"]), "name": m["name"].strip(), "px": num(m["px"]) if m["px"] else None}
    if m["sq"]: return {"op": "sold", "qty": qty(m["sq"]), "name": m["sname"].strip(), "px": num(m["spx"])}
    return {"op": "check", "name": m["cname"].strip()}

def load(p, d):
    try: return json.load(open(p))
    except FileNotFoundError: return d
RES = {r["name"].lower(): r for r in load(os.path.join(HERE, "resources.json"), {"resources": []})["resources"]}
SCH = {}
for s in load(os.path.join(HERE, "schematics.json"), {"schematics": []})["schematics"]: SCH.setdefault(s["name"].lower(), s)
def match(name):
    k = name.lower()
    if k in RES: r = RES[k]; return {"item": r["name"], "type": "resource", "resType": r["type"], "stats": r["stats"]}
    if k in SCH: return {"item": SCH[k]["name"], "type": "crafted"}
    return {"item": name, "type": "other"}

def read_msgs():
    raw = "".join(open(a, encoding="utf-8").read() for a in args) if args else sys.stdin.read()
    raw = raw.strip(); out = []
    if raw.startswith("["): out = json.loads(raw)
    else:
        for line in raw.splitlines():
            if not line.strip(): continue
            if line.lstrip().startswith("{"): out.append(json.loads(line)); continue
            p = line.split("\t", 3)
            if len(p) == 4: out.append({"id": p[0], "timestamp": p[1], "author": p[2], "content": p[3]})
            else: print("skip unparseable line:", line[:80], file=sys.stderr)
    return out

def apply(inv, cmd, msg):
    m = match(cmd["name"]); key = m["item"].lower()
    it = inv["items"].setdefault(key, {**m, "qty": 0, "cpu": None, "qsold": 0, "revenue": 0.0, "costSold": 0.0})
    it.update({k: v for k, v in m.items()})  # refresh stats/type
    q = cmd.get("qty", 0)
    if cmd["op"] == "add":
        if cmd["px"] is not None:
            old = (it["cpu"] or 0) * it["qty"] if it["cpu"] is not None else 0
            it["cpu"] = (old + cmd["px"] * q) / (it["qty"] + q) if it["cpu"] is not None else cmd["px"]
        it["qty"] += q
    elif cmd["op"] == "remove": it["qty"] = max(0, it["qty"] - q)
    elif cmd["op"] == "sold":
        it["qty"] = max(0, it["qty"] - q); it["qsold"] += q; it["revenue"] += q * cmd["px"]; it["costSold"] += q * (it["cpu"] or 0)
    it["updated"] = msg.get("timestamp", "")
    inv["log"].append({"id": str(msg["id"]), "ts": msg.get("timestamp", ""), "cmd": msg["content"], "item": m["item"], "op": cmd["op"]})
    inv["log"] = inv["log"][-500:]

def main():
    inv = load(INV, {"items": {}, "log": []}); inv["items"] = {i["item"].lower(): i for i in inv.get("items", [])} if isinstance(inv.get("items"), list) else inv["items"]
    seen = set(load(SEEN, [])); done = 0
    msgs = sorted(read_msgs(), key=lambda m: (int(m["id"]) if str(m["id"]).isdigit() else 0, m.get("timestamp", "")))
    for msg in msgs:
        mid = str(msg.get("id", "")); c = (msg.get("content") or "").strip()
        if not mid or mid in seen: continue
        if (msg.get("author") or "").strip().lower() != BOT or not c.lower().startswith(PREFIX): continue
        seen.add(mid)
        try: cmd = parse(c)
        except ValueError as e: cmd = None; print("bad command:", c, e, file=sys.stderr)
        if not cmd: print("unrecognized:", c, file=sys.stderr); continue
        if cmd["op"] == "check": print("check (no-op):", c, file=sys.stderr); continue
        apply(inv, cmd, msg); done += 1; print("applied:", c, "->", cmd, file=sys.stderr)
    out = {"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "items": sorted(inv["items"].values(), key=lambda i: i["item"].lower()), "log": inv["log"]}
    if DRY: print(json.dumps(out, indent=1)); return
    json.dump(out, open(INV, "w"), indent=1); json.dump(sorted(seen), open(SEEN, "w"))
    print(f"{done} command(s) applied", file=sys.stderr)
    if "--push" in sys.argv and done:
        g = lambda *a: subprocess.run(["git", "-C", HERE, *a], check=True)
        g("pull", "-q", "--rebase", "--autostash"); g("add", "inventory.json")
        if subprocess.run(["git", "-C", HERE, "diff", "--cached", "--quiet"]).returncode: g("commit", "-q", "-m", "Update inventory.json from game chat"); g("push", "-q")
main()
