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
import plog
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

    want = {'buggy': 'fail', 'fixed': 'pass'}

    def run_and_log(name, repo):
        status, det = run_version(repo, gen_test)
        expect = want.get(name, 'fail')  # llm variants should fail
        mark = 'OK ' if status == expect else '!! '
        plog.log(f'    {mark}{name:<28} -> {status}  (want {expect})')
        return status, det

    versions['buggy'], detail['buggy'] = run_and_log('buggy', buggy)
    versions['fixed'], detail['fixed'] = run_and_log('fixed', fixed)

    llm_variants = []
    for llm_key, _patch in incorrect:
        vdir = path.join(repos_dir, csv_data.repo_llm(pid, bug, llm_key))
        if not path.isdir(vdir):
            plog.log(f'    -- buggy_{llm_key} (checkout missing, skipped)')
            continue
        name = f'buggy_{llm_key}'
        versions[name], detail[name] = run_and_log(name, vdir)
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
    ap.add_argument('-b', '--bug', help='restrict to one bug id (use with -p)')
    ap.add_argument('--records-dir',
                    help='per-bug artifact dir to merge eval into '
                         '(default <tests-dir>/records)')
    args = ap.parse_args()
    records_dir = args.records_dir or path.join(args.tests_dir, 'records')

    bugs = csv_data.load_incorrect(args.csv)
    targets = [(b, i) for b, i in csv_data.select(bugs, args.project, args.bug)
               if path.isfile(path.join(args.tests_dir, f"{i['pid']}_{i['bug']}_aug.txt"))]
    plog.log(f'evaluating {len(targets)} augmented test(s)')

    results = {}
    n_correct = 0
    for idx, (bug_id, info) in enumerate(targets, 1):
        pid, bug = info['pid'], info['bug']
        test_file = path.join(args.tests_dir, f'{pid}_{bug}_aug.txt')
        with open(test_file) as f:
            gen_test = rp.strip_fences(f.read())

        plog.log(f'=== [eval {idx}/{len(targets)}] {bug_id} ===')
        plog.block('augmented test method', gen_test)
        res = eval_bug(args.repos_dir, pid, bug, info['incorrect'], gen_test)
        if res is None:
            plog.log('    skip: missing buggy/fixed checkout')
            continue
        results[bug_id] = res
        n_correct += int(res['correctly_augmented'])
        verdict = 'CORRECTLY AUGMENTED' if res['correctly_augmented'] else 'not distinguishing'
        plog.log(f'    => {verdict}  ({n_correct} correct so far)')

        # Merge the eval result into the per-bug generation record if present.
        rec_path = path.join(records_dir, f'{pid}_{bug}.json')
        if path.isfile(rec_path):
            try:
                with open(rec_path) as f:
                    rec = json.load(f)
                rec['eval'] = res
                with open(rec_path, 'w') as f:
                    json.dump(rec, f, indent=2)
            except (ValueError, OSError):
                pass
        with open(args.out, 'w') as f:
            json.dump(results, f, indent=2)

    plog.log(f'[done] correctly augmented: {n_correct}/{len(results)} -> {args.out}')


if __name__ == '__main__':
    main()
