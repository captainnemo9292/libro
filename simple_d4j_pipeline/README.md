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

LLM keys are `claude-sonnet-4` and `gemini-3-pro-preview` (the `-ori` columns).

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
# 1. checkouts (shared with Flow 1)
bash checkout_all.sh ./repos

# 2. build buggy_<llm> checkouts from the incorrect patches
python prepare_patched.py --csv eval.csv --repos-dir ./repos
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

### Notes

- **Patch layout**: CSV patches use a normalized `src/main/java/` layout; d4j
  checkouts use the project's real source root. `patch_utils.py` reduces each
  header to its package path, locates the real file, and applies hunks fuzzily,
  so the layouts don't need to match.
- **Production-only by default**: `prepare_patched.py` applies only
  non-test source sections of an LLM patch. Use `--include-test-changes` to
  also apply test-file edits.
- **Model**: `--model` defaults to `gpt-5.4-mini`; the call uses the OpenAI
  Responses API with a chat-completions fallback.

## File map

| File | Role |
|------|------|
| `checkout_all.sh` | checkout buggy + fixed for all/selected bugs |
| `injector.py` | resolve imports + inject a method into the best test class |
| `run_pipeline.py` | FIB runner (inject/compile/run on buggy+fixed) |
| `csv_data.py` | read the eval CSV; bugs with incorrect `-ori` patches |
| `patch_utils.py` | preprocess + apply LLM patches to a checkout |
| `prepare_patched.py` | build `buggy_<llm>` checkouts |
| `d4j_tests.py` | extract bug-triggering developer test methods |
| `java_utils.py` | literal/comment-aware Java brace matching |
| `gen_augmented.py` | build prompt + query OpenAI + parse the test method |
| `eval_augmented.py` | run augmented test on buggy/fixed/buggy_<llm> |
| `report_stats.py` | aggregate success stats |
