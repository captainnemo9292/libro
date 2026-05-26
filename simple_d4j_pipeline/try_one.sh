#!/usr/bin/env bash
#
# Smoke-test the augmented-test pipeline end-to-end on ONE Defects4J bug.
# Runs: checkout (buggy+fixed) -> build buggy_<llm> -> generate test -> evaluate.
#
# Usage:
#   OPENAI_API_KEY=... bash try_one.sh <Project> <bug> [REPOS_DIR] [TESTS_DIR] [CSV] [MODEL]
#
# Examples:
#   OPENAI_API_KEY=sk-... bash try_one.sh Chart 16
#   bash try_one.sh Lang 6 /tmp/repos /tmp/aug_tests
#
# The bug must have >=1 incorrect LLM patch in the CSV, otherwise steps 2-4
# will report 0 targets. Set JDK 8 first for Defects4J.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PID="${1:?usage: try_one.sh <Project> <bug> [REPOS_DIR] [TESTS_DIR] [CSV] [MODEL]}"
BUG="${2:?usage: try_one.sh <Project> <bug> [REPOS_DIR] [TESTS_DIR] [CSV] [MODEL]}"
REPOS_DIR="${3:-$(pwd)/repos}"
TESTS_DIR="${4:-$(pwd)/aug_tests}"
CSV="${5:-$HERE/data/LLM_patch_manual_evaluation.csv}"
MODEL="${6:-gpt-5.4-mini}"

echo "### bug=${PID}-${BUG}  repos=${REPOS_DIR}  tests=${TESTS_DIR}  model=${MODEL}"

echo "### [1/4] checkout buggy + fixed"
mkdir -p "$REPOS_DIR"
[ -d "$REPOS_DIR/${PID}_${BUG}b" ] || defects4j checkout -p "$PID" -v "${BUG}b" -w "$REPOS_DIR/${PID}_${BUG}b"
[ -d "$REPOS_DIR/${PID}_${BUG}f" ] || defects4j checkout -p "$PID" -v "${BUG}f" -w "$REPOS_DIR/${PID}_${BUG}f"

echo "### [2/4] build buggy_<llm> from incorrect patch(es)"
python "$HERE/prepare_patched.py" --csv "$CSV" --repos-dir "$REPOS_DIR" -p "$PID" -b "$BUG"

echo "### [3/4] generate augmented test (OpenAI)"
python "$HERE/gen_augmented.py" --csv "$CSV" --repos-dir "$REPOS_DIR" \
    --tests-dir "$TESTS_DIR" -p "$PID" -b "$BUG" --model "$MODEL" --save-prompts

echo "### [4/4] evaluate on buggy / fixed / buggy_<llm>"
python "$HERE/eval_augmented.py" --csv "$CSV" --repos-dir "$REPOS_DIR" \
    --tests-dir "$TESTS_DIR" -p "$PID" -b "$BUG" \
    --out "$TESTS_DIR/${PID}_${BUG}_result.json"

echo "### result JSON: $TESTS_DIR/${PID}_${BUG}_result.json"
cat "$TESTS_DIR/${PID}_${BUG}_result.json"
