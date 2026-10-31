"""Read-only disk inventory of the project folder.

Writes reports into inventory/ only. Never deletes or modifies anything.
Never reads the contents of .env*/credential/settings.json files.
Never follows symlinks/junctions (reparse points). Standard library only.
Run from the project root:  python scripts/inventory.py
"""

import csv
import hashlib
import heapq
import json
import os
import platform
import re
import shutil
import stat
from dubtool import proc  # every external process goes through the helper (AGU6 task 1)
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.abspath(os.getcwd())
OUT = os.path.join(ROOT, "inventory")
LP = "\\\\?\\"

REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

UNREAD = []           # (rel_path, error)
LINKS = []            # rel paths of symlinks/junctions/reparse points
DIR_STATS = {}        # rel(dir) -> dict(size, count, newest, oldest, own_mtime)
SP_DIRS = set()       # rel dirs that are .../Lib/site-packages
SP_FILES = {}         # rel sp-dir -> [(rel_child_file, size, mtime)]
BIG_FILES = []        # heap of (size, rel) -> top 50 largest
ALLFILE_ROWS = []     # rows [rel, size, mtime] for 02_files.csv
DUP_CAND = {}         # size -> [rel] candidates >=1MB for 03
DIST_FILE_META = {}   # rel under dist/ -> (size, mtime) for 07
VENV_ROOT_DIRS = []   # rel dirs that directly contain pyvenv.cfg
EXT_STATS = {}

BIG_THRESHOLD = 5 * 1024 * 1024
DUP_MIN = 1024 * 1024

ENV_FILE_RE = re.compile(r"(^|/)\.env")
CRED_RE = re.compile(
    r"(^|/)[^/]*key[^/]*$|(^|/)[^/]*secret[^/]*$|(^|/)[^/]*credential[^/]*$"
    r"|\.pem$|\.pfx$|\.p12$|\.kdbx$|\.token$|(^|/)settings\.json$",
    re.IGNORECASE,
)

SOURCE_TOPS = {"src", "tests", "scripts", "packaging", "docs", "agent", ".vscode"}
DATA_TOPS = {"jobs", "models", "sample", "review", "screenshots", "excerpts", "eval", "vendor"}
BUILD_TOPS = {"dist", "build"}
CACHE_TOPS = {".venv", ".venv-build", "venv", ".pytest_cache", ".freebuff", "__pycache__"}
BUILD_LOG_RE = re.compile(r"^(build_|exe_build_.*\.log$|cleanup)", re.IGNORECASE)
SPECIFIED_PREFIXES = ("src/", "tests/", "scripts/", "packaging/", "docs/")


def iso(ts):
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return "unknown"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def top_cat(rel):
    first = rel.split("/", 1)[0]
    if first == ".git":
        return "vcs"
    if first in CACHE_TOPS:
        return "cache"
    if first in SOURCE_TOPS:
        return "source"
    if first in BUILD_TOPS:
        return "build-output"
    if first in DATA_TOPS:
        return "data"
    if first == ".env" or first.startswith(".env."):
        return "env"
    if first in ("config.yaml", "pyproject.toml", "glossary.example.json", "README.md",
                 "README_BUILD.md", "REPORT_UPDATE_7.md", "REPORT_UPDATE_8.md",
                 "REVIEW_NOTES.md", ".gitignore", ".env.example"):
        return "source"
    if BUILD_LOG_RE.match(first):
        return "build-output"
    return "other"


def file_cat(rel):
    first = rel.split("/", 1)[0]
    if first == ".git":
        return "vcs"
    if "__pycache__" in rel:
        return "cache"
    if first in CACHE_TOPS:
        return "cache"
    if first in SOURCE_TOPS:
        return "source"
    if first in BUILD_TOPS or re.search(r"\.(exe|dll|zip|log)$", rel, re.I):
        return "build-output"
    if first in DATA_TOPS:
        return "data"
    if first == ".env" or first.startswith(".env."):
        return "env"
    if "/" not in rel:
        if BUILD_LOG_RE.match(first):
            return "build-output"
        if first in ("config.yaml", "pyproject.toml", "glossary.example.json", "README.md",
                     "README_BUILD.md", "REPORT_UPDATE_7.md", "REPORT_UPDATE_8.md",
                     "REVIEW_NOTES.md", ".gitignore"):
            return "source"
        return "other"
    return "other"


def ext_of(name):
    dot = name.rfind(".")
    if 0 < dot < len(name) - 1:
        return name[dot:].lower()
    return ""


def is_reparse(st, is_symlink):
    if is_symlink:
        return True
    if hasattr(st, "st_file_attributes"):
        return bool(st.st_file_attributes & REPARSE_FLAG)
    return False


def walk_dir(absp, rel):
    """Post-order walk. Returns dict(size, count, newest, oldest, own_mtime).
    Never follows reparse points (symlinks/junctions); records unreadables."""
    own_mtime = 0.0
    try:
        st = os.lstat(absp)
        own_mtime = st.st_mtime
    except OSError as e:
        UNREAD.append((rel or "<root>", "lstat: " + str(e)))
    size = 0
    count = 0
    newest = 0.0
    oldest = float("inf")

    try:
        it = os.scandir(absp)
    except OSError as e:
        UNREAD.append((rel or "<root>", "scandir: " + str(e)))
        return {"size": 0, "count": 0, "newest": newest, "oldest": oldest, "own_mtime": own_mtime}
    with it:
        for entry in it:
            name = entry.name
            erel = (rel + "/" + name) if rel else name
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as e:
                UNREAD.append((erel, "stat: " + str(e)))
                continue
            if is_reparse(st, entry.is_symlink()):
                LINKS.append(erel)
                continue
            if entry.is_dir(follow_symlinks=False):
                sub = walk_dir(entry.path, erel)
                DIR_STATS[erel] = sub
                size += sub["size"]
                count += sub["count"]
                if sub["newest"]:
                    newest = max(newest, sub["newest"])
                if sub["oldest"] is not None and sub["oldest"] != float("inf"):
                    oldest = min(oldest, sub["oldest"])
                if name == "site-packages":
                    SP_DIRS.add(erel)
                if name == "pyvenv.cfg":
                    VENV_ROOT_DIRS.append(rel)
            else:
                sz = st.st_size
                mt = st.st_mtime
                size += sz
                count += 1
                newest = max(newest, mt)
                if oldest == float("inf") or mt < oldest:
                    oldest = mt
                nm = entry.name
                e_ext = ext_of(nm).lower()
                e1, e2 = EXT_STATS.get(e_ext, (0, 0))
                EXT_STATS[e_ext] = (e1 + 1, e2 + sz)

                if len(BIG_FILES) < 50:
                    heapq.heappush(BIG_FILES, (sz, erel))
                elif sz > BIG_FILES[0][0]:
                    heapq.heapreplace(BIG_FILES, (sz, erel))

                if sz >= DUP_MIN and not ENV_FILE_RE.search(erel) and not CRED_RE.search(erel):
                    DUP_CAND.setdefault(sz, []).append(erel)

                in_venv = any(erel == v or erel.startswith(v + "/") for v in VENV_ROOT_DIRS)
                if not in_venv:
                    if sz >= BIG_THRESHOLD or "/" not in erel \
                            or erel.startswith(SPECIFIED_PREFIXES):
                        ALLFILE_ROWS.append([erel, sz, mt])
                if rel == "dist" or rel.startswith("dist/"):
                    DIST_FILE_META[erel] = (sz, mt)
                if rel in SP_DIRS:
                    SP_FILES.setdefault(rel, []).append((erel, sz, mt))
    return {"size": size, "count": count,
            "newest": (newest if newest else own_mtime),
            "oldest": (None if oldest == float("inf") else oldest), "own_mtime": own_mtime}


def scoped_size(absp):
    """Aggregated size/count/newest for an outside folder; no reparse follow."""
    size = 0
    count = 0
    newest = 0.0
    try:
        it = os.scandir(absp)
    except OSError as e:
        UNREAD.append((absp, "scandir(outside): " + str(e)))
        return {"size": 0, "count": 0, "newest": 0.0}
    with it:
        for entry in it:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as e:
                UNREAD.append((entry.path, "stat(outside): " + str(e)))
                continue
            if is_reparse(st, entry.is_symlink()):
                LINKS.append(entry.path)
                continue
            if entry.is_dir(follow_symlinks=False):
                sub = scoped_size(entry.path)
                size += sub["size"]
                count += sub["count"]
                newest = max(newest, sub["newest"] or sub["mtime"])
            else:
                size += st.st_size
                count += 1
                newest = max(newest, st.st_mtime)
    own = 0.0
    try:
        own = os.lstat(absp).st_mtime
    except OSError:
        pass
    return {"size": size, "count": count, "newest": newest, "mtime": own}


def summary_markdown(root_stat, total, files_all, top_entries, top_file_meta):
    lines = []
    a = lines.append
    a("# Project inventory summary (facts, read-only scan)")
    a("")
    a(f"- Date: {now_iso()}")
    a(f"- OS: {platform.platform()}")
    a(f"- Python: {sys.version.replace(chr(10), ' ')}")
    a(f"- Project path: {ROOT}")
    a(f"- Drive total: {root_stat.total:,} bytes; drive free: {root_stat.free:,} bytes")
    a(f"- Project total size: {total:,} bytes ({total/1048576:.1f} MB)")
    a(f"- Total files: {files_all:,}")
    a(f"- Total directories: {len(DIR_STATS):,}")
    a(f"- Symlinks/junctions/reparse points: {len(LINKS)}")
    a(f"- Unreadable paths: {len(UNREAD)}")
    if LINKS:
        a("")
        for l in LINKS[:200]:
            a(f"    link: {l}")
    if UNREAD:
        a("")
        for p, e in UNREAD[:200]:
            a(f"    unreadable: {p} -> not measured: {e}")
    a("")
    a("## Every top-level entry, sorted by size descending")
    a("")
    a("| path | size_bytes | size_MB | file_count | newest_mtime | category |")
    a("|---|---|---|---|---|---|")
    for rel in top_entries:
        if rel in top_file_meta:
            cat = file_cat(rel)
            sz = top_file_meta[rel]["size"]
            cnt = 1
            newest = iso(top_file_meta[rel]["mtime"])
        else:
            ds = DIR_STATS[rel]
            cat = top_cat(rel)
            sz = ds["size"]
            cnt = ds["count"]
            newest = iso(ds["newest"])
        a(f"| {rel} | {sz:,} | {sz/1048576:.2f} | {cnt:,} | {newest} | {cat} |")
    a("")
    cat_sum = {}
    cat_share_note = {}
    for rel in top_entries:
        cat = file_cat(rel) if rel in top_file_meta else top_cat(rel)
        sz = top_file_meta[rel]["size"] if rel in top_file_meta else DIR_STATS[rel]["size"]
        if cat not in cat_sum:
            cat_sum[cat] = 0
        cat_sum[cat] += sz
    top_total = sum(cat_sum.values())
    a("## By category (aggregated over top-level entries)")
    a("")
    a("| category | bytes | MB | share of project |")
    a("|---|---|---|---|")
    for cat, s in sorted(cat_sum.items(), key=lambda kv: -kv[1]):
        a(f"| {cat} | {s:,} | {s/1048576:.2f} | {100.0*s/top_total:.1f}% |")
    a("")
    a("## Top 30 file extensions by total size (all files)")
    a("")
    a("| extension | file count | bytes |")
    a("|---|---|---|")
    for ext, (c, s) in sorted(EXT_STATS.items(), key=lambda kv: -kv[1][1])[:30]:
        a(f"| {ext or '(none)'} | {c:,} | {s:,} |")
    a("")
    a("## 50 largest individual files")
    a("")
    a("| path | size_bytes | mtime |")
    a("|---|---|---|")
    mtmap = {}
    for rel, _sz, mt in ALLFILE_ROWS:
        mtmap[rel] = mt
    for sz, rel in sorted(BIG_FILES, key=lambda t: -t[0]):
        mt = mtmap.get(rel)
        a(f"| {rel} | {sz:,} | {iso(mt) if mt else 'not measured: mtime not collected for this file'} |")
    a("")
    a(f"Duplicate groups (files >=1 MB, same size and sha256): {len(DUP_GROUPS)} groups; "
      f"bytes reclaimable by keeping one copy per group: {DUP_FREED:,}")
    a("")
    a("Thresholds:")
    a(f"- 02_files.csv 'large file' threshold outside src/tests/scripts/packaging/docs and root: "
      f"{BIG_THRESHOLD:,} bytes ({BIG_THRESHOLD/1048576:.0f} MB)")
    a("")
    return "\n".join(lines)


def write_dirs_csv():
    rows = []
    seen = set()
    for rel, ds in DIR_STATS.items():
        depth = rel.count("/") + 1
        if depth <= 2:
            rows.append((rel, ds))
            seen.add(rel)
    for rel, ds in DIR_STATS.items():
        if rel not in seen and ds["size"] >= 10 * 1024 * 1024:
            rows.append((rel, ds))
            seen.add(rel)
    ve_rows = []
    for sp_rel in sorted(SP_DIRS):
        sp_depth = sp_rel.count("/")
        for child_rel in [c for c in DIR_STATS
                          if c.startswith(sp_rel + "/") and c.count("/") == sp_depth + 1]:
            ve_rows.append((child_rel, DIR_STATS[child_rel]))
        for child, sz, mt in SP_FILES.get(sp_rel, []):
            ve_rows.append((child, {"size": sz, "count": 1, "newest": mt, "oldest": mt}))
    rows.sort(key=lambda r: -r[1]["size"])
    ve_rows.sort(key=lambda r: -r[1]["size"])
    path = os.path.join(OUT, "01_dirs.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "size_bytes", "file_count", "newest_mtime", "oldest_mtime", "category"])
        for rel, ds in rows:
            w.writerow([rel, ds["size"], ds["count"], iso(ds["newest"]), iso(ds["oldest"]), top_cat(rel)])
        for rel, ds in ve_rows:
            w.writerow([rel, ds["size"], ds["count"], iso(ds["newest"]), iso(ds["oldest"]), "cache"])
    return len(rows) + len(ve_rows)


def csv0_len_prefilter():
    total = 0
    for rel, sz, mt in ALLFILE_ROWS:
        total += len(f"{rel},{sz},{iso(mt)},{ext_of(rel)},{file_cat(rel)}") + 1
    return total


def write_files_csv():
    global BIG_THRESHOLD, ALLFILE_ROWS
    while csv0_len_prefilter() > 3 * 1024 * 1024 and BIG_THRESHOLD < 1024 ** 3:
        BIG_THRESHOLD = max(BIG_THRESHOLD * 4, 20 * 1024 * 1024)
        ALLFILE_ROWS = [r for r in ALLFILE_ROWS
                        if r[1] >= BIG_THRESHOLD or "/" not in r[0] or r[0].startswith(SPECIFIED_PREFIXES)
                        or r[0] == "dist"]
    rows = sorted(ALLFILE_ROWS, key=lambda r: -r[1])
    path = os.path.join(OUT, "02_files.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "size_bytes", "mtime", "extension", "category"])
        for rel, sz, mt in rows:
            w.writerow([rel, sz, iso(mt), ext_of(rel.rsplit("/", 1)[-1]), file_cat(rel)])
    return len(rows)


DUP_GROUPS = []
DUP_FREED = 0


def write_duplicates_csv():
    global DUP_GROUPS, DUP_FREED
    sizes = [s for s in DUP_CAND if len(DUP_CAND[s]) > 1]
    sizes.sort()
    groups = []
    for sz in sizes:
        hashes = {}
        for rel in DUP_CAND[sz]:
            digest = None
            try:
                h = hashlib.sha256()
                with open(LP + os.path.normpath(os.path.join(ROOT, rel)), "rb") as fp:
                    while True:
                        b = fp.read(1024 * 1024)
                        if not b:
                            break
                        h.update(b)
                digest = h.hexdigest()
            except OSError as e:
                UNREAD.append((rel, "hash: " + str(e)))
                continue
            hashes.setdefault(digest, []).append(rel)
        for digest, rels in hashes.items():
            if digest and len(rels) > 1:
                groups.append((sz, digest, rels))
    groups.sort(key=lambda g: (g[0], g[2][0]))
    rows = []
    gid = 0
    for sz, digest, rels in groups:
        gid += 1
        DUP_FREED += sz * (len(rels) - 1)
        for rel in sorted(rels):
            rows.append([gid, sz, digest, rel])
    DUP_GROUPS = groups
    path = os.path.join(OUT, "03_duplicates.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group_id", "size_bytes", "sha256", "path"])
        for r in rows:
            w.writerow(r)
    return len(rows)


def job_rows(jobs_root, location):
    rows = []
    if not os.path.isdir(jobs_root):
        return rows
    try:
        names = sorted(os.listdir(jobs_root))
    except OSError as e:
        UNREAD.append((jobs_root, str(e)))
        return rows
    for name in names:
        jdir = os.path.join(jobs_root, name)
        if not os.path.isdir(jdir):
            continue
        if location.startswith("jobs"):
            rel = location + "/" + name
            ds = {"size": 0, "count": 0}
            info = DIR_STATS.get(rel)
            ds = info if info else scoped_size(jdir)
            size, cnt, newest = ds["size"], ds["count"], ds["newest"]
        else:
            info = scoped_size(jdir)
            size, cnt, newest = info["size"], info["count"], info["newest"]
        source = ""
        stages_txt = ""
        state_path = os.path.join(jdir, "state.json")
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if isinstance(data.get("source"), str):
                    source = data["source"]
                if isinstance(data.get("stages"), dict):
                    stages_txt = ";".join(f"{k}={v}" for k, v in data["stages"].items()
                                          if isinstance(k, str) and isinstance(v, str))
        except (OSError, ValueError) as e:
            UNREAD.append((location + "/" + name + "/state.json", "read: " + str(e)))
        age = (time.time() - newest) / 86400 if newest else ""
        rows.append([name, location, size, cnt,
                     iso(newest) if newest else "not measured: no readable mtime",
                     round(age, 1) if age != "" else "", source, stages_txt])
    return rows


def write_jobs_csv():
    rows = job_rows(os.path.join(ROOT, "jobs"), "jobs")
    lapp = os.environ.get("LOCALAPPDATA", "")
    appd = os.environ.get("APPDATA", "")
    if lapp and os.path.isdir(os.path.join(lapp, "DubTool", "jobs")):
        rows.extend(job_rows(os.path.join(lapp, "DubTool", "jobs"), "%LOCALAPPDATA%/DubTool/jobs"))
    if appd and os.path.isdir(os.path.join(appd, "DubTool", "jobs")):
        rows.extend(job_rows(os.path.join(appd, "DubTool", "jobs"), "%APPDATA%/DubTool/jobs"))
    path = os.path.join(OUT, "04_jobs.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job_id", "location", "size_bytes", "file_count", "newest_mtime",
                    "age_days", "source", "stages(status words only)"])
        for r in rows:
            w.writerow(r)
    return len(rows)


def host_of(url):
    m = re.match(r"(?:[^:/?#]+://)?(?:[^/@]+@)?([^/@]+)", url)
    return m.group(1) if m else url


def git_facts_to_file():
    dest = os.path.join(OUT, "05_git.txt")
    lines = []
    a = lines.append
    a("git inventory (read-only facts)")
    a("generated: " + now_iso())
    r = proc.run(["git", "count-objects", "-vH"], cwd=ROOT)
    a("--- git count-objects -vH ---")
    a(r.stdout.strip() or r.stderr.strip())
    r = proc.run(["git", "rev-list", "--all", "--count"], cwd=ROOT)
    a(f"commits (rev-list --all --count): {r.stdout.strip()}")
    r = proc.run(["git", "branch", "--all"], cwd=ROOT)
    n_branch = sum(1 for l in r.stdout.splitlines() if l.strip())
    a(f"branches (git branch --all): {n_branch}")
    r = proc.run(["git", "remote", "get-url", "origin"], cwd=ROOT)
    url = r.stdout.strip()
    a("remote: " + ("origin -> host " + host_of(url) if url else "none"))
    rev = proc.run(["git", "rev-list", "--objects", "--all"], cwd=ROOT)
    oid_path = []
    for line in rev.stdout.splitlines():
        if line.strip():
            parts = line.split(" ", 1)
            oid_path.append((parts[0], parts[1] if len(parts) > 1 else ""))
    batch_in = "\n".join(o + " " + p if p else o for o, p in oid_path) + "\n"
    bc = proc.run(
        ["git", "cat-file", "--batch-check=%(objecttype) %(objectname) %(objectsize) %(rest)"],
        cwd=ROOT, input=batch_in)
    objs = []
    for line in bc.stdout.splitlines():
        parts = line.split(" ", 3)
        if len(parts) >= 3:
            try:
                sz = int(parts[2])
            except ValueError:
                continue
            rest = parts[3] if len(parts) > 3 else ""
            objs.append((sz, parts[0], rest))
    a("--- 30 largest objects in the whole history ---")
    a("bytes | type | path (or oid if no path)")
    for sz, t, rest in sorted(objs, key=lambda o: -o[0])[:30]:
        a(f"{sz} | {t} | {rest}")
    pat = re.compile(
        r"\.(exe|dll|zip|mp4|wav|mp3|onnx|bin|pt|safetensors)$"
        r"|(^|/)(dist|build|models|vendor|\.venv[^/]*)/", re.IGNORECASE)
    seen = {}
    for sz, t, rest in objs:
        if t != "blob" or not rest:
            continue
        p = rest.replace("\\", "/")
        if pat.search(p):
            prev = seen.get(p)
            seen[p] = (sz, (prev[1] + 1) if prev else 1)
    a("--- every path in history matching *.exe *.dll *.zip *.mp4 *.wav *.mp3 *.onnx *.bin *.pt"
      " *.safetensors or under dist/ build/ models/ vendor/ .venv* (path | size | versions) ---")
    for p, (sz, n) in sorted(seen.items()):
        a(f"{p} | {sz}"
          + (f" | versions_in_history={n}" if n > 1 else ""))
    # loose objects not reachable from any ref (dangling/unreachable)
    reachable = {o for o, _p in oid_path}
    all_obj = proc.run(
        ["git", "cat-file", "--batch-all-objects",
         "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        cwd=ROOT)
    unreachable = []
    loose_total = 0
    loose_n = 0
    for line in all_obj.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        oid, t, s = parts[0], parts[1], parts[2]
        try:
            sz = int(s)
        except ValueError:
            continue
        loose_n += 1
        loose_total += sz
        if t == "blob" and sz >= 1024 * 1024 and oid not in reachable:
            unreachable.append((sz, oid))
    a("--- loose objects in .git not reachable from any ref (blob >= 1 MB) ---")
    a(f"all objects counted: {loose_n}; their total bytes: {loose_total:,}")
    for sz, oid in sorted(unreachable, reverse=True):
        a(f"{oid} | {sz}")
    a("(paths of unreachable blobs are unknown by construction; list above covers blobs >= 1 MB)")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return dest


def write_outside_csv():
    rows = []
    lapp = os.environ.get("LOCALAPPDATA", "")
    appd = os.environ.get("APPDATA", "")
    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    home = os.path.expanduser("~")

    def add(label, path):
        if not path:
            rows.append([label, "", "", "not measured: path not found"])
        elif os.path.isdir(path):
            info = scoped_size(path)
            rows.append([label, info["size"], info["count"],
                         iso(info["newest"]) if info["newest"] else "not measured: no readable mtime"])
        elif os.path.isfile(path):
            st = os.stat(path)
            rows.append([label, st.st_size, 1, iso(st.st_mtime)])
        else:
            rows.append([label, "", "", "not measured: path not found"])

    add("%LOCALAPPDATA%/DubTool", os.path.join(lapp, "DubTool") if lapp else "")
    for sub in ("jobs", "models", "logs", "cache"):
        add(f"%LOCALAPPDATA%/DubTool/{sub}", os.path.join(lapp, "DubTool", sub) if lapp else "")
    add("%APPDATA%/DubTool", os.path.join(appd, "DubTool") if appd else "")
    hf_home = os.environ.get("HF_HOME", "")
    add("HuggingFace cache ($HF_HOME)" if hf_home else "HuggingFace cache (~/.cache/huggingface)",
        hf_home if hf_home else os.path.join(home, ".cache", "huggingface"))
    add("pip cache (candidate %LOCALAPPDATA%/pip/cache; running a pip command is not allowed for this task)",
        os.path.join(lapp, "pip", "cache"))
    add("%LOCALAPPDATA%/pyinstaller", os.path.join(lapp, "pyinstaller"))
    pref = re.compile(r"dubtool|ag6|ag7|ag8|ag9|san|pyinstaller", re.IGNORECASE)
    if tmp and os.path.isdir(tmp):
        try:
            for name in os.listdir(tmp):
                if pref.search(name):
                    add(f"%TEMP%/{name}", os.path.join(tmp, name))
        except OSError as e:
            UNREAD.append((tmp, str(e)))
    add("Python installation in use (sys.base_prefix aggregate)", sys.base_prefix)
    path = os.path.join(OUT, "06_outside_project.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "size_bytes", "file_count", "newest_mtime"])
        for r in rows:
            w.writerow(r)
    return rows


def write_dist_csv():
    rows = []
    dist_dir = os.path.join(ROOT, "dist")
    if os.path.isdir(dist_dir):
        for name in sorted(os.listdir(dist_dir)):
            rel = "dist/" + name
            if os.path.isdir(os.path.join(dist_dir, name)) and rel in DIR_STATS:
                ds = DIR_STATS[rel]
                rows.append([rel, ds["size"], iso(ds["newest"])])
            elif os.path.isfile(os.path.join(dist_dir, name)) and rel in DIST_FILE_META:
                sz, mt = DIST_FILE_META[rel]
                rows.append([rel, sz, iso(mt)])
    for rel in sorted(DIST_FILE_META):
        parts = rel.split("/")
        if len(parts) >= 3 and parts[1].startswith("DubTool") \
                and DIST_FILE_META[rel][0] >= 5 * 1024 * 1024:
            rows.append([rel, DIST_FILE_META[rel][0], iso(DIST_FILE_META[rel][1])])
    for rel, (sz, mt) in sorted(DIST_FILE_META.items()):
        if rel.lower().endswith(".zip") and not any(r[0] == rel for r in rows):
            rows.append([rel, sz, iso(mt)])
    path = os.path.join(OUT, "07_dist.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "size_bytes", "mtime"])
        for r in rows:
            w.writerow(r)
    return len(rows)


def main():
    global ALLFILE_ROWS
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    root_stat = shutil.disk_usage(ROOT)
    result = walk_dir(LP + ROOT, "")
    TOTAL = result["size"]
    FILE_COUNT = result["count"]

    venv_prefixes = []
    for v in set(VENV_ROOT_DIRS):
        venv_prefixes.append(v + "/")

    def in_venv(rel):
        return any(rel.startswith(p) for p in venv_prefixes)

    if venv_prefixes:
        ALLFILE_ROWS = [r for r in ALLFILE_ROWS if not in_venv(r[0])]
        for sz in list(DUP_CAND):
            kept = [rel for rel in DUP_CAND[sz] if not in_venv(rel)]
            if kept:
                DUP_CAND[sz] = kept
            else:
                DUP_CAND.pop(sz)

    # top-level entries
    top_entries = []
    top_file_meta = {}
    for name in os.listdir(ROOT):
        rel = name
        full = os.path.join(ROOT, name)
        try:
            st = os.lstat(LP + full)
        except OSError as e:
            UNREAD.append((name, str(e)))
            continue
        if rel in DIR_STATS and not top_file_meta.get(name):
            top_entries.append(rel)
        else:
            top_file_meta[name] = {"size": st.st_size, "mtime": st.st_mtime}
            top_entries.append(name)
    top_entries.sort(key=lambda r: -(top_file_meta[r]["size"] if r in top_file_meta
                                     else DIR_STATS[r]["size"]))

    n_dup = write_duplicates_csv()
    with open(os.path.join(OUT, "00_summary.md"), "w", encoding="utf-8") as f:
        f.write(summary_markdown(root_stat, TOTAL, FILE_COUNT, top_entries, top_file_meta))

    outside_rows = write_outside_csv()
    n_dirs = write_dirs_csv()
    n_files = write_files_csv()
    n_jobs = write_jobs_csv()
    git_dest = git_facts_to_file()
    n_dist = write_dist_csv()

    print(f"[inventory] scan seconds: {time.time()-t0:.1f}")
    print(f"[inventory] total project bytes: {TOTAL:,}  files: {FILE_COUNT:,}  dirs: {len(DIR_STATS):,}")
    print(f"[inventory] symlinks/junctions/reparse: {len(LINKS)}  unreadable: {len(UNREAD)}")
    print(f"[inventory] rows: 01_dirs={n_dirs} 02_files={n_files} 03_dup={n_dup} 04_jobs={n_jobs} 07_dist={n_dist}")
    print(f"[inventory] 02 threshold used: {BIG_THRESHOLD:,} bytes ({BIG_THRESHOLD/1048576:.0f} MB)")
    print(f"[inventory] git report: {git_dest}")
    print(f"[inventory] duplicate groups: {len(DUP_GROUPS):,}; bytes reclaimable by keeping one copy per group: {DUP_FREED:,}")
    for r in outside_rows:
        print("[inventory] outside:", r)
    for p, e in UNREAD[:100]:
        print("[inventory] UNREAD:", p, "->", e)


if __name__ == "__main__":
    main()
