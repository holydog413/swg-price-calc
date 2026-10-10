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
  inv set <name> [qty=<n>] [cpu=<x>] [sold=<n>] [avgsold=<x>]   overwrite fields
  inv del <name>               delete the item
Quantities: 200000, 200k, 1.5m.  Options: --push (commit+push tools/inventory.enc.json in dkd-guild), --dry-run.
The inventory is stored only encrypted (AES-256-GCM, key = PBKDF2-SHA256 of $DKD_INV_PASSWORD, random salt).
"""
import json, re, os, sys, subprocess, datetime
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from dkd_target import OUT, push, REPO
import base64, secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
INV, SEEN = os.path.join(OUT, "inventory.enc.json"), os.path.join(HERE, "inv_seen.json")
ITER = 310000
b64, unb64 = (lambda b: base64.b64encode(b).decode()), base64.b64decode
def _key(pw, salt, it): return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=it).derive(pw.encode())
def pw():
    p = os.environ.get("DKD_INV_PASSWORD")
    if not p: sys.exit("DKD_INV_PASSWORD is not set")
    return p
def load_inv():
    if not os.path.exists(INV): return {"items": [], "log": []}
    e = json.load(open(INV))
    return json.loads(AESGCM(_key(pw(), unb64(e["salt"]), e["iter"])).decrypt(unb64(e["iv"]), unb64(e["ct"]), None))
def save_inv(obj):
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    ct = AESGCM(_key(pw(), salt, ITER)).encrypt(iv, json.dumps(obj, separators=(",", ":")).encode(), None)
    json.dump({"v": 1, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": ITER, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}, open(INV, "w"))
BOT, PREFIX = "uprising bot", "rover: inv"
args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i-1] != "--delete-item"]; DRY = "--dry-run" in sys.argv

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

SETRE = re.compile(r"^rover:\s*inv\s+set\s+(?P<name>.+?)\s+(?P<kv>(?:\w+\s*=\s*\S+\s*)+)$", re.I)
DELRE = re.compile(r"^rover:\s*inv\s+del(?:ete)?\s+(?P<name>.+?)\s*$", re.I)
SETKEYS = {"qty": "qty", "cpu": "cpu", "sold": "qsold", "qsold": "qsold", "avgsold": "avgsold", "soldcpu": "avgsold"}
def parse_ext(content):
    c = content.strip()
    if (m := DELRE.match(c)): return {"op": "del", "name": m["name"]}
    if (m := SETRE.match(c)):
        f = {}
        for k, v in re.findall(r"(\w+)\s*=\s*(\S+)", m["kv"]):
            if k.lower() not in SETKEYS: raise ValueError(f"unknown field {k!r}")
            f[SETKEYS[k.lower()]] = num(v)
        return {"op": "set", "name": m["name"], "fields": f}
    return parse(c)

def load(p, d):
    try: return json.load(open(p))
    except FileNotFoundError: return d
RES = {r["name"].lower(): r for r in load(os.path.join(OUT, "resources.json"), {"resources": []})["resources"]}
SCH = {}
for s in load(os.path.join(OUT, "schematics.json"), {"schematics": []})["schematics"]: SCH.setdefault(s["name"].lower(), s)
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
    if cmd["op"] == "del":
        if key not in inv["items"]: key = next((k for k in inv["items"] if k == cmd["name"].lower()), key)
        inv["items"].pop(key, None)
        inv["log"].append({"id": str(msg["id"]), "ts": msg.get("timestamp", ""), "cmd": msg["content"], "item": m["item"], "op": "del"}); return
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
    elif cmd["op"] == "set": set_fields(it, cmd["fields"])
    it["updated"] = msg.get("timestamp", "")
    inv["log"].append({"id": str(msg["id"]), "ts": msg.get("timestamp", ""), "cmd": msg["content"], "item": m["item"], "op": cmd["op"]})
    inv["log"] = inv["log"][-500:]

def set_fields(it, f):
    """Overwrite qty / cpu / qsold / avgsold; realized cost of sold units keeps its per-unit basis."""
    basis = it["costSold"] / it["qsold"] if it.get("qsold") else (it.get("cpu") or 0)
    avg = it["revenue"] / it["qsold"] if it.get("qsold") else 0
    if "qty" in f: it["qty"] = max(0, round(f["qty"]))
    if "cpu" in f: it["cpu"] = f["cpu"]
    if "qsold" in f: it["qsold"] = max(0, round(f["qsold"]))
    if "avgsold" in f: avg = f["avgsold"]
    if not it.get("costSold") and "cpu" in f: basis = f["cpu"]
    it["revenue"] = it["qsold"] * avg; it["costSold"] = it["qsold"] * basis

REL = "tools/inventory.enc.json"
def g(*a, check=True): return subprocess.run(["git", "-C", REPO, *a], check=check, capture_output=True, text=True)
def sync_latest():
    """Re-read the newest committed inventory (the page may have saved via the GitHub API) before writing."""
    g("fetch", "-q", "origin", "main"); g("checkout", "origin/main", "--", REL)

def main():
    if "--delete-item" in sys.argv:   # maintenance: drop an item (and its log lines) entirely
        name = sys.argv[sys.argv.index("--delete-item") + 1].lower(); inv = load_inv()
        inv["items"] = [i for i in inv["items"] if i["item"].lower() != name]; inv["log"] = [l for l in inv["log"] if l["item"].lower() != name]
        save_inv(inv); print("deleted", name, file=sys.stderr)
        if "--push" in sys.argv: push(["inventory.enc.json"], "Update tools/inventory.enc.json")
        return
    if "--init" in sys.argv and not os.path.exists(INV): save_inv({"updated": None, "items": [], "log": []}); print("initialized", INV, file=sys.stderr); return
    seen = set(load(SEEN, []))
    cmds = []
    msgs = sorted(read_msgs(), key=lambda m: (int(m["id"]) if str(m["id"]).isdigit() else 0, m.get("timestamp", "")))
    for msg in msgs:
        mid = str(msg.get("id", "")); c = (msg.get("content") or "").strip()
        if not mid or mid in seen: continue
        if (msg.get("author") or "").strip().lower() != BOT or not c.lower().startswith(PREFIX): continue
        seen.add(mid)
        try: cmd = parse_ext(c)
        except ValueError as e: cmd = None; print("bad command:", c, e, file=sys.stderr)
        if not cmd: print("unrecognized:", c, file=sys.stderr); continue
        if cmd["op"] == "check": print("check (no-op):", c, file=sys.stderr); continue
        cmds.append((cmd, msg)); print("parsed:", c, "->", cmd, file=sys.stderr)
    PUSH = "--push" in sys.argv and cmds and not DRY
    for attempt in range(5):
        if PUSH: sync_latest()
        inv = load_inv(); inv["items"] = {i["item"].lower(): i for i in inv.get("items", [])}
        for cmd, msg in cmds: apply(inv, cmd, msg)
        out = {"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "items": sorted(inv["items"].values(), key=lambda i: i["item"].lower()), "log": inv["log"][-500:]}
        if DRY: print(json.dumps(out, indent=1)); return
        save_inv(out)
        if not PUSH: break
        g("add", REL); g("commit", "-q", "-m", "Update tools/inventory.enc.json from game chat", "--", REL)
        if g("pull", "-q", "--rebase", "--autostash", "origin", "main", check=False).returncode != 0:
            g("rebase", "--abort", check=False)   # the file changed upstream since we read it
        elif g("push", "-q", "origin", "HEAD:main", check=False).returncode == 0: break
        g("reset", "-q", "HEAD~1")   # someone (e.g. the web page) saved in between: drop our commit, re-read, re-apply
        print("push rejected, retrying with latest file", file=sys.stderr)
    else: sys.exit("could not push after 5 attempts; seen-state not updated")
    json.dump(sorted(seen), open(SEEN, "w"))
    print(f"{len(cmds)} command(s) applied", file=sys.stderr)
main()
