"""Run LLM-generated Defects4J tests and report fail-in-buggy / pass-in-fixed.

A simplified re-implementation of libro's `postprocess_d4j.py`. For each bug it
injects every candidate test method into BOTH the buggy and the fixed checkout,
compiles, runs the single test via `defects4j test -t`, and marks a candidate
"success" when it fails on the buggy version but passes on the fixed version
(i.e. a genuine bug-reproducing test).

Expected on-disk layout (produced by checkout_all.sh):
    REPOS_DIR/<Project>_<bug>b   buggy checkout
    REPOS_DIR/<Project>_<bug>f   fixed checkout

Generated tests (one bare JUnit method per file, optionally ```-fenced):
    TESTS_DIR/<Project>_<bug>_*.txt
e.g.  Time_18_n0.txt, Time_18_n1.txt, Lang_5_attempt3.txt ...

Usage:
    # everything found in the tests dir
    python run_pipeline.py --tests-dir ./gen_tests --repos-dir ./repos --out results.json

    # a single bug (handy for debugging)
    python run_pipeline.py --tests-dir ./gen_tests --repos-dir ./repos -p Time -b 18

Results are written incrementally to --out, so the run can be interrupted and
resumed-by-rerun without losing completed bugs.
"""

import os
import glob
import json
import argparse
import subprocess as sp
from os import path

import injector
import plog


TEST_TIMEOUT = '1m'   # per-test wall-clock cap passed to `timeout`


def strip_fences(text):
    """Remove a leading ``` / ```java fence line and a trailing ``` fence."""
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else ''
    if text.rstrip().endswith('```'):
        text = text.rstrip()[:-3]
    return text.strip()


def export_dir(repo, prop):
    """Return a repo-relative dir (with trailing slash) from defects4j export."""
    p = sp.run(['defects4j', 'export', '-p', prop], capture_output=True, cwd=repo)
    return p.stdout.decode('utf-8', 'ignore').strip().rstrip('/') + '/'


def git_reset_clean(repo):
    sp.run(['git', 'reset', '--hard', 'HEAD'], cwd=repo,
           stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    sp.run(['git', 'clean', '-df'], cwd=repo,
           stdout=sp.DEVNULL, stderr=sp.DEVNULL)


def compile_repo(repo):
    """Return (returncode, filtered_compile_error_message)."""
    proc = sp.run(['defects4j', 'compile'],
                  stdout=sp.PIPE, stderr=sp.PIPE, cwd=repo)
    lines = proc.stderr.decode('utf-8', 'ignore').split('\n')[2:]
    lines = [e for e in lines
             if '[javac] [' not in e and '[javac]' in e
             and 'warning:' not in e and '[javac] Note:' not in e
             and 'compiler be upgraded.' not in e]
    return proc.returncode, '\n'.join(lines)


def run_test(repo, test_name):
    """Run one test. Returns (status, failed_tests).

    status:  0 normal,  -1 no output (likely runtime/timeout failure)."""
    proc = sp.run(['timeout', TEST_TIMEOUT, 'defects4j', 'test', '-t', test_name],
                  capture_output=True, cwd=repo)
    out = proc.stdout.decode('utf-8', 'ignore')
    if len(out) == 0:
        return -1, []
    lines = out.split('\n')
    failed_tests = [e.strip(' - ') for e in lines[1:] if len(e) > 1]
    return 0, failed_tests


def run_one(repo, gen_test, src_dir, test_dir):
    """Inject + compile + run a single candidate test in one checkout."""
    git_reset_clean(repo)
    gen_test = injector.enforce_static_assertions(gen_test)
    test_name = injector.inject(repo, src_dir, test_dir, gen_test)

    returncode, compile_msg = compile_repo(repo)
    if returncode != 0:
        return {
            'compile_error': True, 'runtime_error': False,
            'failed_tests': [], 'autogen_failed': False,
            'error_msg': None, 'compile_msg': compile_msg,
        }

    status, failed_tests = run_test(repo, test_name)
    error_msg = None
    if failed_tests:
        ft_path = path.join(repo, 'failing_tests')
        if path.exists(ft_path):
            with open(ft_path) as f:
                error_msg = ''.join(f.readlines()[:5])
    return {
        'compile_error': False, 'runtime_error': status == -1,
        'failed_tests': failed_tests, 'autogen_failed': len(failed_tests) > 0,
        'error_msg': error_msg, 'compile_msg': None,
    }


def fails_autogen(info):
    return any('AutoGen' in t for t in info['failed_tests'])


def eval_bug(proj, bug, tests_dir, repos_dir):
    """Evaluate all candidate tests for one bug. Returns {filename: result}."""
    buggy = path.join(repos_dir, f'{proj}_{bug}b')
    fixed = path.join(repos_dir, f'{proj}_{bug}f')
    if not path.isdir(buggy) or not path.isdir(fixed):
        print(f'[skip] missing buggy/fixed checkout for {proj}-{bug}')
        return None

    test_files = sorted(glob.glob(path.join(tests_dir, f'{proj}_{bug}_*.txt')))
    if not test_files:
        return None

    tests = []
    for fp in test_files:
        with open(fp) as f:
            tests.append(strip_fences(f.read()))

    plog.log(f'    {len(tests)} candidate test(s)')
    per_version = {}
    for tag, repo in (('buggy', buggy), ('fixed', fixed)):
        src_dir = export_dir(repo, 'dir.src.classes')
        test_dir = export_dir(repo, 'dir.src.tests')
        outcomes = []
        for i, gen_test in enumerate(tests):
            try:
                outcomes.append(run_one(repo, gen_test, src_dir, test_dir))
            except Exception as e:  # parse / injection failure -> record, keep going
                outcomes.append(f'[error] {repr(e)}')
        per_version[tag] = outcomes

    results = {}
    for fp, b_info, f_info in zip(test_files, per_version['buggy'], per_version['fixed']):
        name = path.basename(fp)
        if isinstance(b_info, str) or isinstance(f_info, str):
            results[name] = {'buggy': b_info, 'fixed': f_info, 'success': False}
            plog.log(f'    {name}: error  buggy={b_info if isinstance(b_info,str) else "ok"} '
                     f'fixed={f_info if isinstance(f_info,str) else "ok"}')
            continue
        success = fails_autogen(b_info) and not fails_autogen(f_info)
        plog.log(f'    {name}: buggy={"fail" if fails_autogen(b_info) else "pass"} '
                 f'fixed={"fail" if fails_autogen(f_info) else "pass"} -> success={success}')
        results[name] = {'buggy': b_info, 'fixed': f_info, 'success': success}
    return results


def discover_bugs(tests_dir):
    """Infer (project, bug) pairs from generated-test filenames."""
    keys = set()
    for fp in glob.glob(path.join(tests_dir, '*.txt')):
        parts = path.basename(fp).split('_')
        if len(parts) >= 3 and parts[1].isdigit():
            keys.add((parts[0], parts[1]))
    return sorted(keys, key=lambda x: (x[0], int(x[1])))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tests-dir', required=True,
                    help='dir with <Project>_<bug>_*.txt generated test methods')
    ap.add_argument('--repos-dir', required=True,
                    help='dir with <Project>_<bug>b / <Project>_<bug>f checkouts')
    ap.add_argument('--out', default='results.json', help='output JSON path')
    ap.add_argument('-p', '--project', help='restrict to one project')
    ap.add_argument('-b', '--bug', help='restrict to one bug id (needs -p)')
    args = ap.parse_args()

    if args.project and args.bug:
        bugs = [(args.project, args.bug)]
    else:
        bugs = discover_bugs(args.tests_dir)

    plog.log(f'running FIB evaluation for {len(bugs)} bug(s)')
    all_results = {}
    n_success = 0
    for idx, (proj, bug) in enumerate(bugs, 1):
        plog.log(f'=== [{idx}/{len(bugs)}] {proj}-{bug} ===')
        res = eval_bug(proj, bug, args.tests_dir, args.repos_dir)
        if res is None:
            continue
        all_results[f'{proj}_{bug}'] = res
        bug_success = sum(1 for r in res.values() if r['success'])
        n_success += bug_success
        plog.log(f'    {bug_success}/{len(res)} candidate test(s) reproduce the bug')
        with open(args.out, 'w') as f:
            json.dump(all_results, f, indent=2)

    plog.log(f'[done] {n_success} reproducing tests across {len(all_results)} bugs '
             f'-> {args.out}')


if __name__ == '__main__':
    main()
