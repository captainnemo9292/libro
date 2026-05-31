#!/usr/bin/env bash
#
# Check out the buggy AND fixed versions of every Defects4J bug (or a subset)
# into a flat repos directory, ready for the test-running pipeline.
#
# Layout produced:
#   REPOS_DIR/<Project>_<bug>b   -> buggy   checkout  (defects4j checkout -v Nb)
#   REPOS_DIR/<Project>_<bug>f   -> fixed   checkout  (defects4j checkout -v Nf)
#
# Usage:
#   bash checkout_all.sh [REPOS_DIR] [PROJECT ...]
#
# Examples:
#   bash checkout_all.sh ./repos                # all projects, all bugs
#   bash checkout_all.sh ./repos Time Lang      # only Time and Lang
#
# Existing checkouts are skipped, so the script is safe to re-run/resume.

set -u

REPOS_DIR="${1:-$(pwd)/repos}"
shift || true
PROJECTS="$*"

if [ -z "$PROJECTS" ]; then
    PROJECTS="$(defects4j pids)"
fi

D4J_HOME="$(dirname "$(which defects4j)")/../.."

mkdir -p "$REPOS_DIR"

for proj in $PROJECTS; do
    commit_db="$D4J_HOME/framework/projects/$proj/commit-db"
    if [ ! -f "$commit_db" ]; then
        echo "[warn] no commit-db for project '$proj', skipping" >&2
        continue
    fi
    for bug in $(cut -f1 -d',' "$commit_db"); do
        for v in b f; do
            out="$REPOS_DIR/${proj}_${bug}${v}"
            if [ -d "$out" ]; then
                echo "[skip] $out already exists"
                continue
            fi
            echo "[checkout] ${proj}-${bug}${v} -> $out"
            defects4j checkout -p "$proj" -v "${bug}${v}" -w "$out"
        done
    done
done

echo "[done] checkouts under $REPOS_DIR"
