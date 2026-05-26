# Simplified Defects4J test-running pipeline

Standalone scripts (distilled from libro) for running LLM-generated tests
against Defects4J checkouts. There are two flows that share the same
checkout/inject/run machinery:

1. **FIB** — evaluate a bare test method as a bug reproduction
   (fail-in-buggy / pass-in-fixed).
2. **Augmented test** — generate and evaluate a regression test that
   distinguishes the developer fix from both the buggy version *and* an
   incorrect LLM patch (pass on `fixed`, fail on `buggy` and every
   `buggy_<llm>`).

All scripts are meant to run inside the Defects4J container (with the
`defects4j` command on `PATH`).

## Requirements

```bash
pip install javalang        # injection / parsing (run_pipeline, eval_augmented)
pip install openai          # LLM generation only (gen_augmented)
update-alternatives --set java /usr/lib/jvm/java-8-openjdk-amd64/jre/bin/java   # JDK 8 for d4j
```

## On-disk layout

`checkout_all.sh` and `prepare_patched.py` populate a flat `repos/` dir:

```
repos/<Project>_<bug>b              buggy checkout      (defects4j -v Nb)
repos/<Project>_<bug>f              fixed checkout      (defects4j -v Nf)
repos/<Project>_<bug>_<llm>         buggy + incorrect LLM patch (committed)
```

LLM keys match the CSV columns: `claude-sonnet-4-ori`, `claude-sonnet-4-norm`,
`gemini-3-pro-preview-ori`, `gemini-3-pro-preview-norm`. Each incorrect variant
gets its own `buggy_<llm>` checkout, and an augmented test must fail on all of
them.

---

## Flow 1 — bug reproduction (FIB)

Generated tests are one bare JUnit method per file, optionally ```` ``` ````-fenced,
named `<Project>_<bug>_*.txt` (e.g. `Time_18_n0.txt`).

```bash
# 1. check out buggy + fixed for all bugs (or a subset of projects)
bash checkout_all.sh ./repos               # or: bash checkout_all.sh ./repos Time Lang

# 2. inject / compile / run each candidate on both versions
python run_pipeline.py --tests-dir ./gen_tests --repos-dir ./repos --out results.json
#    single bug:  ... -p Time -b 18
```

`success` in `results.json` = test fails on buggy and passes on fixed.

---

## Flow 2 — augmented regression test

Input is the manual-evaluation CSV (incorrect LLM patches + developer fixes).

```bash
# 1. obtain buggy + fixed checkouts
#    (a) fresh from Defects4J:
bash checkout_all.sh ./repos
#    (b) OR import pre-existing checkouts from a d4j_subjects-style tree
#        (<SRC>/<Project>-<bug>/{buggy,fixed}); skips defects4j checkout:
bash import_checkouts.sh .. ./repos

# 2. build buggy_<llm> checkouts from the incorrect patches.
#    --solutions-dir is the SAME parent dir as import_checkouts.sh's SRC: it
#    holds <Project>-<bug>/source_ori-* (-ori) and source_patched_rst-* (-norm).
python prepare_patched.py --csv eval.csv --repos-dir ./repos \
    --solutions-dir ..
#    --solutions-dir PATH    on-disk path matching /data/d4j_subjects/d4j_bugs;
#                            copies each LLM full solution file (robust) instead
#                            of applying the diff. STRONGLY recommended -- the CSV
#                            diffs were generated against a reformatted base and
#                            ~40% fail to apply cleanly. Diff is the fallback.
#    --include-test-changes  also apply the LLM's edits to test files (default: skip)
#    -p Chart                restrict to one project

# 3. generate one augmented test per bug via the OpenAI API
OPENAI_API_KEY=... python gen_augmented.py \
    --csv eval.csv --repos-dir ./repos --tests-dir ./aug_tests --model gpt-5.4-mini
#    --save-prompts   also dump the prompt sent for each bug
#    --overwrite      regenerate existing *_aug.txt

# 4. run each augmented test on buggy / fixed / every buggy_<llm>
python eval_augmented.py \
    --csv eval.csv --repos-dir ./repos --tests-dir ./aug_tests --out aug_results.json

# 5. stats
python report_stats.py --results aug_results.json --by-project
```

A test is **correctly augmented** when it passes on `fixed`, fails on `buggy`,
and fails on every `buggy_<llm>` variant (≥1 variant required).

### Try a single bug first

To sanity-check the whole augmented flow on one bug in one command
(checkout → build `buggy_<llm>` → generate → evaluate → print result):

```bash
OPENAI_API_KEY=... bash try_one.sh Chart 16
# bash try_one.sh <Project> <bug> [REPOS_DIR] [TESTS_DIR] [CSV] [MODEL] [PRICE_IN] [PRICE_OUT]
#
# If you have pre-existing checkouts + solution dirs, point SRC_DIR at their
# parent; buggy/fixed are copied (no defects4j checkout) and SOLUTIONS_DIR
# defaults to it:
#   SRC_DIR=.. OPENAI_API_KEY=... bash try_one.sh Jsoup 68
```

Defaults: `REPOS_DIR=./repos`, `TESTS_DIR=./aug_tests`,
`CSV=data/LLM_patch_manual_evaluation.csv`, `MODEL=gpt-5.4-mini`.
The bug must have ≥1 incorrect LLM patch in the CSV.

Every augmented script also takes `-b/--bug` (with `-p`) to target one bug, so
you can run any single stage on its own, e.g.:

```bash
python prepare_patched.py --csv data/LLM_patch_manual_evaluation.csv --repos-dir ./repos -p Chart -b 16
python eval_augmented.py   --csv data/LLM_patch_manual_evaluation.csv --repos-dir ./repos --tests-dir ./aug_tests -p Chart -b 16
```

For the FIB flow, `run_pipeline.py -p Time -b 18` runs a single bug.

### Cost tracking & saved artifacts

`gen_augmented.py` records token usage and cost for every API call:

- Per-call and cumulative cost are logged live (`[cost] Chart-16: in=… out=… call=$… cumulative=$…`).
- A cumulative cost file is written to `<tests-dir>/records/cost.json`
  (model, prices, totals, and every call), and is resumable across runs.
- A per-bug artifact `<tests-dir>/records/<pid>_<bug>.json` saves the
  **prompt inputs** (developer patch, LLM patches, failing tests), the **full
  prompt**, the **raw reply**, the **generated method**, **token usage**, and
  **cost**. `eval_augmented.py` then merges the **eval result** into the same
  file under an `eval` key.

Pricing is USD per 1M tokens. `gpt-5.4-mini` is built into `cost.PRICING`
(input $0.75, output $4.50, cached input $0.075), so cost — including the
cheaper rate for cached prompt tokens — is computed automatically. For other
models, either add an entry to `cost.PRICING` or pass prices explicitly:

```bash
python gen_augmented.py ... --price-in 0.75 --price-out 4.50 --price-cached 0.075
```

Without any pricing (no flags and no `cost.PRICING` entry), token counts are
still recorded but cost is left `null`.

To reconcile against OpenAI's actually-billed cost, `org_costs.py` queries the
Organization Costs API (needs an admin key):

```bash
OPENAI_ADMIN_KEY=sk-admin-... python org_costs.py --days 1
```

### Notes

- **Patch layout**: CSV patches use a normalized `src/main/java/` layout; d4j
  checkouts use the project's real source root. `patch_utils.py` reduces each
  header to its package path and locates the real file, so the layouts don't
  need to match.
- **Reformatted base**: the CSV diffs were generated against a reformatted
  "restore" base, so applying them to the d4j buggy source fails on reflowed
  lines (~40% of patches). Pass `--solutions-dir` so each changed file is
  replaced wholesale with the LLM's full solution file (the solution path is
  read from the diff's `+++` header, so the on-disk model-dir name -- e.g.
  `source_ori-gemini-3-pro` -- need not match the CSV column name).
- **Production-only by default**: `prepare_patched.py` applies only
  non-test source sections of an LLM patch. Use `--include-test-changes` to
  also apply test-file edits.
- **Model**: `--model` defaults to `gpt-5.4-mini`; the call uses the OpenAI
  Responses API with a chat-completions fallback.

## File map

| File | Role |
|------|------|
| `try_one.sh` | end-to-end smoke test of the augmented flow on one bug |
| `checkout_all.sh` | checkout buggy + fixed for all/selected bugs |
| `import_checkouts.sh` | import existing buggy/ + fixed/ dirs into the repos/ layout |
| `injector.py` | resolve imports + inject a method into the best test class |
| `run_pipeline.py` | FIB runner (inject/compile/run on buggy+fixed) |
| `csv_data.py` | read the eval CSV; bugs with incorrect `-ori`/`-norm` patches |
| `patch_utils.py` | preprocess + apply LLM patches to a checkout |
| `prepare_patched.py` | build `buggy_<llm>` checkouts |
| `d4j_tests.py` | extract bug-triggering developer test methods |
| `java_utils.py` | literal/comment-aware Java brace matching |
| `gen_augmented.py` | build prompt + query OpenAI + parse the test method; track cost; save artifacts |
| `eval_augmented.py` | run augmented test on buggy/fixed/buggy_<llm>; merge eval into records |
| `cost.py` | token-usage cost computation + cumulative tracker |
| `org_costs.py` | reconcile against OpenAI's billed Organization Costs API |
| `report_stats.py` | aggregate success stats |
