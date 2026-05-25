"""Evaluate augmented tests against buggy, fixed, and every buggy_<llm> variant.

An augmented test is "correctly augmented" when it
  - PASSES on fixed,
  - FAILS on buggy,
  - FAILS on every buggy_<llm> variant of that bug (>=1 variant required).

Reuses the inject/compile/run machinery from run_pipeline.py.

Usage:
    python eval_augmented.py --csv eval.csv --repos-dir ./repos \
        --tests-dir ./aug_tests --out aug_results.json
"""

import json
import argparse
from os import path

import csv_data
import run_pipeline as rp


def classify(info):
    """Map a run_one result to pass / fail / compile_error / error."""
    if isinstance(info, str):
        return 'error'
    if info['compile_error']:
        return 'compile_error'
    if rp.fails_autogen(info):
        return 'fail'
    if info['runtime_error']:
        return 'error'
    return 'pass'


def run_version(repo, gen_test):
    src_dir = rp.export_dir(repo, 'dir.src.classes')
    test_dir = rp.export_dir(repo, 'dir.src.tests')
    try:
        info = rp.run_one(repo, gen_test, src_dir, test_dir)
    except Exception as e:
        return 'error', {'error': repr(e)}
    slim = {} if isinstance(info, str) else {
        'compile_error': info['compile_error'],
        'runtime_error': info['runtime_error'],
        'failed_tests': info['failed_tests'],
    }
    return classify(info), slim


def eval_bug(repos_dir, pid, bug, incorrect, gen_test):
    versions = {}
    detail = {}

    buggy = path.join(repos_dir, csv_data.repo_buggy(pid, bug))
    fixed = path.join(repos_dir, csv_data.repo_fixed(pid, bug))
    if not path.isdir(buggy) or not path.isdir(fixed):
        return None

    versions['buggy'], detail['buggy'] = run_version(buggy, gen_test)
    versions['fixed'], detail['fixed'] = run_version(fixed, gen_test)

    llm_variants = []
    for llm_key, _patch in incorrect:
        vdir = path.join(repos_dir, csv_data.repo_llm(pid, bug, llm_key))
        if not path.isdir(vdir):
            continue
        name = f'buggy_{llm_key}'
        versions[name], detail[name] = run_version(vdir, gen_test)
        llm_variants.append(name)

    correctly_augmented = (
        bool(llm_variants)
        and versions['fixed'] == 'pass'
        and versions['buggy'] == 'fail'
        and all(versions[v] == 'fail' for v in llm_variants)
    )
    return {
        'pid': pid, 'bug': bug,
        'versions': versions,
        'llm_variants': llm_variants,
        'correctly_augmented': correctly_augmented,
        'detail': detail,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--csv', required=True)
    ap.add_argument('--repos-dir', required=True)
    ap.add_argument('--tests-dir', required=True, help='dir with *_aug.txt')
    ap.add_argument('--out', default='aug_results.json')
    ap.add_argument('-p', '--project')
    args = ap.parse_args()

    bugs = csv_data.load_incorrect(args.csv)
    results = {}
    n_correct = 0
    for bug_id, info in sorted(bugs.items()):
        if args.project and info['pid'] != args.project:
            continue
        pid, bug = info['pid'], info['bug']
        test_file = path.join(args.tests_dir, f'{pid}_{bug}_aug.txt')
        if not path.isfile(test_file):
            continue
        with open(test_file) as f:
            gen_test = rp.strip_fences(f.read())

        print(f'=== {bug_id} ===', flush=True)
        res = eval_bug(args.repos_dir, pid, bug, info['incorrect'], gen_test)
        if res is None:
            print(f'    [skip] missing buggy/fixed checkout')
            continue
        results[bug_id] = res
        n_correct += int(res['correctly_augmented'])
        print(f"    {res['versions']} -> correct={res['correctly_augmented']}",
              flush=True)
        with open(args.out, 'w') as f:
            json.dump(results, f, indent=2)

    print(f'[done] correctly augmented: {n_correct}/{len(results)} -> {args.out}')


if __name__ == '__main__':
    main()
