"""Generate an augmented regression test per bug via the OpenAI API.

For each bug with >=1 incorrect LLM patch we build the task prompt from:
  - the correct developer patch (CSV),
  - every incorrect LLM patch (CSV),
  - the bug's triggering developer tests (read from the checkout),
query the model, parse out a single test method, and write it to
<tests-dir>/<pid>_<bug>_aug.txt for the eval step.

Needs OPENAI_API_KEY in the environment and the `openai` package.

Usage:
    python gen_augmented.py --csv eval.csv --repos-dir ./repos \
        --tests-dir ./aug_tests --model gpt-5.4-mini
"""

import os
import re
import argparse
from os import path

import csv_data
import d4j_tests
import plog
from java_utils import matching_brace


PROMPT_TEMPLATE = """\
Projects:

- `buggy`: original buggy project
- `fixed`: project patched with the correct developer fix
- `buggy_<llm>`: project patched with an incorrect LLM-generated patch

Goal:
Write an augmented regression test that:

1. PASSES on `fixed`
2. FAILS on `buggy`
3. FAILS on `buggy_<llm>`

Inputs:

Correct Developer Patch:
```diff
{{correct_developer_patch}}
```

Diff of LLM-generated Patch:

```diff
{{llm_generated_patch_diff}}
```

Relevant Failing Developer Tests:

```java
{{relevant_failing_dev_tests}}
```

Task:
Infer the behavioral difference between the correct developer patch and the incorrect LLM patch, then write a NEW augmented test that exercises behavior fixed only by the developer patch.

Guidelines:

* Do NOT duplicate the provided failing tests.
* Target the exact semantic behavior fixed by the developer patch.
* Prefer a minimal deterministic test.
* Use the project's existing test style and APIs.
* The test must pass on `fixed`.
* The test must fail on both `buggy` and `buggy_<llm>`.
* Output ONLY the complete test method.
"""


def build_prompt(info, failing_tests):
    llm_blob = '\n\n'.join(
        f'// ---- incorrect patch from {key} ----\n{patch}'
        for key, patch in info['incorrect'])
    return (PROMPT_TEMPLATE
            .replace('{{correct_developer_patch}}', info['dev_patch'].strip())
            .replace('{{llm_generated_patch_diff}}', llm_blob.strip())
            .replace('{{relevant_failing_dev_tests}}', failing_tests.strip()))


def strip_fences(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else ''
    if text.rstrip().endswith('```'):
        text = text.rstrip()[:-3]
    return text.strip()


def extract_test_method(text):
    """Pull a single Java test method out of the model's reply.

    Handles fenced output, surrounding prose, or a whole class wrapper.
    Returns the method source, or None if no method could be located.
    """
    text = strip_fences(text)
    m = re.search(r'\bvoid\s+\w+\s*\(', text)
    if not m:
        return None
    # Walk backwards over modifiers/annotations to capture the full signature.
    line_start = text.rfind('\n', 0, m.start()) + 1
    sig_start = line_start
    prefix_lines = text[:line_start].rstrip().split('\n')
    while prefix_lines and prefix_lines[-1].lstrip().startswith('@'):
        dropped = prefix_lines.pop()
        sig_start = text.rfind(dropped, 0, sig_start)
    brace = text.find('{', m.end() - 1)
    if brace == -1:
        return None
    end = matching_brace(text, brace)
    if end == -1:
        return None
    return text[sig_start:end + 1].strip()


def query_openai(prompt, model):
    from openai import OpenAI
    client = OpenAI()
    try:
        resp = client.responses.create(model=model, input=prompt)
        return resp.output_text
    except (AttributeError, TypeError):
        resp = client.chat.completions.create(
            model=model, messages=[{'role': 'user', 'content': prompt}])
        return resp.choices[0].message.content


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--csv', required=True)
    ap.add_argument('--repos-dir', required=True)
    ap.add_argument('--tests-dir', required=True, help='output dir for *_aug.txt')
    ap.add_argument('--model', default='gpt-5.4-mini')
    ap.add_argument('-p', '--project', help='restrict to one project')
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--save-prompts', action='store_true',
                    help='also write the prompt to <tests-dir>/<bug>_prompt.txt')
    args = ap.parse_args()

    os.makedirs(args.tests_dir, exist_ok=True)
    bugs = csv_data.load_incorrect(args.csv)
    targets = [(b, i) for b, i in sorted(bugs.items())
               if not args.project or i['pid'] == args.project]
    plog.log(f'generating augmented tests for {len(targets)} bug(s) '
             f'with model={args.model}')

    n_done = n_fail = n_skip = 0
    for idx, (bug_id, info) in enumerate(targets, 1):
        pid, bug = info['pid'], info['bug']
        models = ', '.join(k for k, _ in info['incorrect'])
        plog.log(f'[gen {idx}/{len(targets)}] {bug_id}  incorrect_models=[{models}]')

        out_path = path.join(args.tests_dir, f'{pid}_{bug}_aug.txt')
        if path.exists(out_path) and not args.overwrite:
            plog.log(f'    skip: {path.basename(out_path)} already exists')
            n_skip += 1
            continue

        fixed = path.join(args.repos_dir, csv_data.repo_fixed(pid, bug))
        buggy = path.join(args.repos_dir, csv_data.repo_buggy(pid, bug))
        src_repo = fixed if path.isdir(fixed) else buggy
        if not path.isdir(src_repo):
            plog.log(f'    skip: no checkout to read triggering tests from')
            n_skip += 1
            continue

        failing_tests = d4j_tests.collect_trigger_sources(src_repo)
        n_trigger = failing_tests.count('//') if failing_tests else 0
        plog.log(f'    triggering dev tests: ~{n_trigger} method(s), '
                 f'{len(failing_tests)} chars  (from {path.basename(src_repo)})')
        prompt = build_prompt(info, failing_tests)
        if args.save_prompts:
            with open(path.join(args.tests_dir, f'{pid}_{bug}_prompt.txt'), 'w') as f:
                f.write(prompt)
        plog.log(f'    querying OpenAI ({len(prompt)} char prompt)...')

        try:
            reply = query_openai(prompt, args.model)
        except Exception as e:
            plog.log(f'    FAIL: API error {e!r}')
            n_fail += 1
            continue

        method = extract_test_method(reply)
        if not method:
            plog.log(f'    FAIL: could not parse a test method from reply '
                     f'(raw saved to {path.basename(out_path)}.raw)')
            with open(out_path + '.raw', 'w') as f:
                f.write(reply)
            n_fail += 1
            continue

        with open(out_path, 'w') as f:
            f.write(method)
        plog.block('generated test method', method)
        plog.log(f'    saved -> {path.basename(out_path)}')
        n_done += 1

    plog.log(f'[done] generated={n_done} failed={n_fail} skipped={n_skip}')


if __name__ == '__main__':
    main()
