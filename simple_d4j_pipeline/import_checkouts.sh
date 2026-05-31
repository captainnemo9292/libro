#!/usr/bin/env bash
#
# Import pre-existing buggy/ and fixed/ checkouts into the repos/ layout the
# pipeline expects, instead of running `defects4j checkout`.
#
# Source layout (one directory per bug, e.g. the parent of these scripts'
# workspace, matching /data/d4j_subjects/d4j_bugs):
#   SRC_DIR/<Project>-<bug>/buggy     a Defects4J working directory
#   SRC_DIR/<Project>-<bug>/fixed
# Produces:
#   REPOS_DIR/<Project>_<bug>b
#   REPOS_DIR/<Project>_<bug>f
#
# The same SRC_DIR is what you pass to prepare_patched.py as --solutions-dir
# (the LLM solution dirs <Project>-<bug>/source_ori-* and source_patched_rst-*
# live alongside buggy/ and fixed/).
#
# Usage:
#   bash import_checkouts.sh <SRC_DIR> [REPOS_DIR] [FILTER ...]
# FILTER matches a project (e.g. Jsoup) or a full bug id (e.g. Chart-16).
#
# Examples:
#   bash import_checkouts.sh .. ./repos
#   bash import_checkouts.sh /home/secollab ./repos Jsoup Chart-16

set -u

SRC_DIR="${1:?usage: import_checkouts.sh <SRC_DIR> [REPOS_DIR] [FILTER ...]}"
shift
REPOS_DIR="${1:-$(pwd)/repos}"
[ $# -gt 0 ] && shift || true
FILTER="$*"

matches() {   # $1 = bug name like Chart-16 ; uses global FILTER
    local name="$1" pid="${1%-*}" tok
    [ -z "$FILTER" ] && return 0
    for tok in $FILTER; do
        [ "$tok" = "$name" ] && return 0
        [ "$tok" = "$pid" ] && return 0
    done
    return 1
}

mkdir -p "$REPOS_DIR"
imported=0 skipped=0 missing=0 warned=0
for bugdir in "$SRC_DIR"/*-*/; do
    [ -d "$bugdir" ] || continue
    name="$(basename "$bugdir")"
    pid="${name%-*}"
    num="${name##*-}"
    [[ "$num" =~ ^[0-9]+$ ]] || continue
    matches "$name" || continue

    for pair in buggy:b fixed:f; do
        sub="${pair%%:*}"
        suf="${pair##*:}"
        src="${bugdir}${sub}"
        dest="$REPOS_DIR/${pid}_${num}${suf}"
        if [ ! -d "$src" ]; then
            echo "[miss] $name: no $sub/"
            missing=$((missing + 1))
            continue
        fi
        if [ -d "$dest" ]; then
            skipped=$((skipped + 1))
            continue
        fi
        if [ ! -f "$src/.defects4j.config" ]; then
            echo "[warn] $src has no .defects4j.config (defects4j commands may fail)"
            warned=$((warned + 1))
        fi
        cp -a "$src" "$dest"
        echo "[ok]   $name/$sub -> ${pid}_${num}${suf}"
        imported=$((imported + 1))
    done
done
echo "[done] imported=$imported skipped=$skipped missing=$missing warned=$warned -> $REPOS_DIR"
