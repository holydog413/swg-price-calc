"""Shared output target: the calculator now lives in the dkd-guild repo under /tools/."""
import os, subprocess
REPO = os.environ.get("DKD_REPO", "/workspace/dkd-guild")
OUT = os.path.join(REPO, "tools")
def push(files, msg):
    g = lambda *a: subprocess.run(["git", "-C", REPO, *a], check=True)
    g("add", *[os.path.join(OUT, f) for f in files])
    if subprocess.run(["git", "-C", REPO, "diff", "--cached", "--quiet"]).returncode:
        g("commit", "-q", "-m", msg)
        g("pull", "-q", "--rebase", "--autostash"); g("push", "-q")
