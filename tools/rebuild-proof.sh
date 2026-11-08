#!/usr/bin/env bash
# Behaviour proof for restructuring data/: run the full database rebuild on a
# scratch restore of the newest archive, using a copy of the current worktree,
# and check the data and exported site files came out unchanged.
#
# Never touches the live database. The copy gets its own .env, PYTHONPATH makes
# the copy's modules win over any editable install of the real tree, and a guard
# refuses to run unless the configured database is the scratch one.
#
# Usage, from a worktree (uncommitted changes included):
#   rebuild-proof.sh [archive.db.gz]
# Output goes under the worktree's git dir and is removed afterwards unless
# KEEP=1. Exits 0 only when every check passes.
set -euo pipefail

root=$(git rev-parse --show-toplevel)
gitdir=$(git rev-parse --absolute-git-dir)
work="$gitdir/rebuild-proof"
archive_dir="${ELECTIONS_ARCHIVE_DIR:-$HOME/dbs/elections}"
archive="${1:-$(ls "$archive_dir"/elections-*.db.gz | tail -1)}"
python="$root/data/election_data/bin/python"
[[ -x $python ]] || python="$root/data/.venv/bin/python"

rm -rf "$work"; mkdir -p "$work/db" "$work/code" "$work/arch"
trap '[[ "${KEEP:-0}" == 1 ]] || rm -rf "$work"' EXIT

echo "Copying worktree..."
(cd "$root" && git ls-files -co --exclude-standard -z | tar --null -T - -cf - 2>/dev/null) | tar -xf - -C "$work/code"
db="$work/db/elections.db"
printf 'DATABASE_PATH=%s\nELECTIONS_ARCHIVE_DIR=%s\n' "$db" "$work/arch" >"$work/code/.env"

echo "Restoring $(basename "$archive") to scratch..."
gzip -dc "$archive" >"$db"

export PYTHONPATH="$work/code/data" DATABASE_PATH="$db" ELECTIONS_ARCHIVE_DIR="$work/arch"
cd "$work/code/data"

"$python" - "$work" "$db" <<'PY'
import importlib, json, pathlib, sqlite3, sys
work, db = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
for name in ("core.config", "config"):
    try:
        cfg = importlib.import_module(name)
        break
    except ModuleNotFoundError:
        continue
else:
    sys.exit("guard: no config module found")
if not pathlib.Path(cfg.__file__).resolve().is_relative_to((work / "code").resolve()):
    sys.exit(f"guard: config imported from outside the copy: {cfg.__file__}")
if pathlib.Path(cfg.DatabaseConfig.from_env().database_path).resolve() != db.resolve():
    sys.exit(f"guard: database is not the scratch copy: {cfg.DatabaseConfig.from_env().database_path}")
c = sqlite3.connect(db)
if c.execute("pragma integrity_check").fetchone()[0] != "ok":
    sys.exit("guard: restored archive failed its integrity check")
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
json.dump({
    "counts": {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables},
    "votes": c.execute("select election_id,count(*),coalesce(sum(vote_total),0) from votes group by election_id order by 1").fetchall(),
}, open(work / "before.json", "w"))
print("guard ok")
PY

echo "Rebuilding..."
status=0
"$python" scripts/rebuild_database.py >"$work/rebuild.log" 2>&1 || status=$?
tail -1 "$work/rebuild.log"
grep -E '^\s+\[(FAIL|ERR)' "$work/rebuild.log" || true

"$python" - "$work" "$db" "$status" "$root" <<'PY'
import filecmp, json, pathlib, sqlite3, sys
work, db, status, root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), int(sys.argv[3]), pathlib.Path(sys.argv[4])
before = json.load(open(work / "before.json"))
c = sqlite3.connect(db)
bad = 0
if status:
    print("FAIL rebuild exited", status); bad += 1
for table, n in before["counts"].items():
    now = c.execute(f'select count(*) from "{table}"').fetchone()[0]
    if now != n:
        print(f"FAIL {table}: {n} -> {now}"); bad += 1
votes = [list(r) for r in c.execute("select election_id,count(*),coalesce(sum(vote_total),0) from votes group by election_id order by 1")]
if votes != before["votes"]:
    print("FAIL per-election vote counts or totals differ"); bad += 1
def walk(cmp, rel=""):
    out = [f"{rel}{n}" for n in cmp.left_only + cmp.right_only + cmp.diff_files]
    for name, sub in cmp.subdirs.items():
        out += walk(sub, f"{rel}{name}/")
    return out
for page in ("electionmaps/data", "uselectionmaps/data"):
    filecmp.clear_cache()
    diffs = walk(filecmp.dircmp(root / page, work / "code" / page))
    if diffs:
        print(f"FAIL {page} differs from the worktree:", *diffs[:20], sep="\n  "); bad += 1
print(f"tables {len(before['counts'])}, elections {len(votes)}")
print("PROOF PASSED" if not bad else f"PROOF FAILED ({bad})")
sys.exit(1 if bad else 0)
PY
