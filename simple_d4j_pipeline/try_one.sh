#!/usr/bin/env bash
#
# Smoke-test the augmented-test pipeline end-to-end on ONE Defects4J bug.
# Runs: checkout (buggy+fixed) -> build buggy_<llm> -> generate test -> evaluate.
#
# Usage:
#   OPENAI_API_KEY=... bash try_one.sh <Project> <bug> [REPOS_DIR] [TESTS_DIR] [CSV] [MODEL] [PRICE_IN] [PRICE_OUT]
#
# PRICE_IN / PRICE_OUT are USD per 1M input/output tokens (for cost tracking).
#
# Examples:
#   OPENAI_API_KEY=sk-... bash try_one.sh Chart 16
#   bash try_one.sh Lang 6 /tmp/repos /tmp/aug_tests "" gpt-5.4-mini 0.25 2.00
#
# The bug must have >=1 incorrect LLM patch in the CSV, otherwise steps 2-4
# will report 0 targets. Set JDK 8 first for Defects4J.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PID="${1:?usage: try_one.sh <Project> <bug> [REPOS_DIR] [TESTS_DIR] [CSV] [MODEL] [PRICE_IN] [PRICE_OUT]}"
BUG="${2:?usage: try_one.sh <Project> <bug> [REPOS_DIR] [TESTS_DIR] [CSV] [MODEL] [PRICE_IN] [PRICE_OUT]}"
REPOS_DIR="${3:-$(pwd)/repos}"
TESTS_DIR="${4:-$(pwd)/aug_tests}"
CSV="${5:-$HERE/data/LLM_patch_manual_evaluation.csv}"
MODEL="${6:-gpt-5.4-mini}"
PRICE_IN="${7:-}"
PRICE_OUT="${8:-}"

PRICE_ARGS=()
[ -n "$PRICE_IN" ] && PRICE_ARGS+=(--price-in "$PRICE_IN")
[ -n "$PRICE_OUT" ] && PRICE_ARGS+=(--price-out "$PRICE_OUT")

# Optional: env SRC_DIR = parent dir holding <Project>-<bug>/{buggy,fixed} and
# the LLM solution dirs (matching /data/d4j_subjects/d4j_bugs). When set, buggy/
# fixed are copied from there (no defects4j checkout) and SOLUTIONS_DIR defaults
# to it for full-file LLM solution replacement.
SRC_DIR="${SRC_DIR:-}"
SOLUTIONS_DIR="${SOLUTIONS_DIR:-$SRC_DIR}"
PREP_ARGS=()
[ -n "$SOLUTIONS_DIR" ] && PREP_ARGS+=(--solutions-dir "$SOLUTIONS_DIR")

echo "### bug=${PID}-${BUG}  repos=${REPOS_DIR}  tests=${TESTS_DIR}  model=${MODEL}"

echo "### [1/4] obtain buggy + fixed"
mkdir -p "$REPOS_DIR"
for pair in buggy:b fixed:f; do
    sub="${pair%%:*}"; suf="${pair##*:}"
    dest="$REPOS_DIR/${PID}_${BUG}${suf}"
    [ -d "$dest" ] && continue
    if [ -n "$SRC_DIR" ] && [ -d "$SRC_DIR/${PID}-${BUG}/$sub" ]; then
        cp -a "$SRC_DIR/${PID}-${BUG}/$sub" "$dest"
        echo "    copied $SRC_DIR/${PID}-${BUG}/$sub -> $(basename "$dest")"
    else
        defects4j checkout -p "$PID" -v "${BUG}${suf}" -w "$dest"
    fi
done

echo "### [2/4] build buggy_<llm> from incorrect patch(es)"
python "$HERE/prepare_patched.py" --csv "$CSV" --repos-dir "$REPOS_DIR" -p "$PID" -b "$BUG" \
    ${PREP_ARGS[@]+"${PREP_ARGS[@]}"}

echo "### [3/4] generate augmented test (OpenAI)"
python "$HERE/gen_augmented.py" --csv "$CSV" --repos-dir "$REPOS_DIR" \
    --tests-dir "$TESTS_DIR" -p "$PID" -b "$BUG" --model "$MODEL" --save-prompts \
    ${PRICE_ARGS[@]+"${PRICE_ARGS[@]}"}

echo "### [4/4] evaluate on buggy / fixed / buggy_<llm>"
python "$HERE/eval_augmented.py" --csv "$CSV" --repos-dir "$REPOS_DIR" \
    --tests-dir "$TESTS_DIR" -p "$PID" -b "$BUG" \
    --out "$TESTS_DIR/${PID}_${BUG}_result.json"

echo "### result JSON: $TESTS_DIR/${PID}_${BUG}_result.json"
cat "$TESTS_DIR/${PID}_${BUG}_result.json"
echo
echo "### cost summary: $TESTS_DIR/records/cost.json"
cat "$TESTS_DIR/records/cost.json" 2>/dev/null || echo "(no cost file)"
echo
echo "### full per-bug record: $TESTS_DIR/records/${PID}_${BUG}.json"
