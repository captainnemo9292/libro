"""Run the augmented-test pipeline on k diverse bugs across several models.

Selects k bugs (default 30) that have >=1 incorrect patch, spread across
projects (round-robin, deterministic with --seed). Builds the shared checkouts
and buggy_<llm> variants ONCE (they don't depend on the generation model), then
for each generation model runs generation + evaluation into its own folder:

    <out-dir>/selected_bugs.csv      the filtered CSV driving every step
    <out-dir>/<model>/aug_tests/     generated tests + records/ + cost.json
    <out-dir>/<model>/aug_results.json
    <out-dir>/summary.json           correct/total + cost per model

Usage:
    OPENAI_API_KEY=... python try_k.py --csv eval.csv \
        --models gpt-5-mini gpt-5.2 gpt-5.4-mini \
        --src-dir .. --out-dir ./results_k -k 30

--src-dir is the parent holding <Project>-<bug>/{buggy,fixed,source_*}; when
set, checkouts are copied from there and it doubles as --solutions-dir.
"""

import os
import sys
import csv
import json
import random
import argparse
import subprocess as sp
from os import path
from collections import defaultdict

import csv_data
import plog

HERE = path.dirname(path.abspath(__file__))


def select_diverse(bugs, k, seed):
    """Pick k bug ids spread across projects (round-robin, seeded)."""
    rng = random.Random(seed)
    by_proj = defaultdict(list)
    for bug_id, info in bugs.items():
        by_proj[info['pid']].append(bug_id)
    for ids in by_proj.values():
        rng.shuffle(ids)
    projects = sorted(by_proj)
    rng.shuffle(projects)

    selected = []
    while len(selected) < k:
        progressed = False
        for p in projects:
            if by_proj[p]:
                selected.append(by_proj[p].pop())
                progressed = True
                if len(selected) >= k:
                    break
        if not progressed:
            break
    return selected


def write_filtered_csv(csv_path, keep_ids, out_path):
    with open(csv_path, newline='') as f:
        rows = list(csv.reader(f))
    keep = set(keep_ids)
    out_rows = [rows[0]] + [r for r in rows[1:] if len(r) > 1 and r[1] in keep]
    with open(out_path, 'w', newline='') as f:
        csv.writer(f).writerows(out_rows)


def ensure_checkouts(repos_dir, selected, bugs, src_dir):
    for bug_id in selected:
        info = bugs[bug_id]
        pid, bug = info['pid'], info['bug']
        for sub, suf in (('buggy', 'b'), ('fixed', 'f')):
            dest = path.join(repos_dir, f'{pid}_{bug}{suf}')
            if path.isdir(dest):
                continue
            src = path.join(src_dir, f'{pid}-{bug}', sub) if src_dir else None
            if src and path.isdir(src):
                sp.run(['cp', '-a', src, dest], check=True)
            else:
                sp.run(['defects4j', 'checkout', '-p', pid, '-v', f'{bug}{suf}',
                        '-w', dest], check=True)


def run(cmd):
    plog.log('$ ' + ' '.join(cmd))
    sp.run(cmd, check=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--csv', required=True)
    ap.add_argument('--models', nargs='+', required=True,
                    help='generation models to sweep (space- or comma-separated)')
    ap.add_argument('-k', '--num', type=int, default=30)
    ap.add_argument('--repos-dir', default='./repos', help='shared checkouts dir')
    ap.add_argument('--out-dir', default='./results_k')
    ap.add_argument('--src-dir',
                    help='parent of <Project>-<bug>/{buggy,fixed,source_*}; '
                         'copies checkouts from here and is used as --solutions-dir')
    ap.add_argument('--solutions-dir', help='override (default: --src-dir)')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--include-test-changes', action='store_true')
    args = ap.parse_args()

    models = []
    for m in args.models:
        models += [x for x in m.split(',') if x]
    solutions_dir = args.solutions_dir or args.src_dir

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(args.repos_dir, exist_ok=True)

    bugs = csv_data.load_incorrect(args.csv)
    selected = select_diverse(bugs, args.num, args.seed)
    n_proj = len(set(b.rsplit('-', 1)[0] for b in selected))
    plog.log(f'selected {len(selected)}/{len(bugs)} bugs across {n_proj} projects '
             f'(seed={args.seed})')
    plog.log(f'  {selected}')

    sel_csv = path.join(args.out_dir, 'selected_bugs.csv')
    write_filtered_csv(args.csv, selected, sel_csv)

    plog.log('=== checkouts (shared) ===')
    ensure_checkouts(args.repos_dir, selected, bugs, args.src_dir)

    plog.log('=== build buggy_<llm> (shared) ===')
    prep = [sys.executable, path.join(HERE, 'prepare_patched.py'),
            '--csv', sel_csv, '--repos-dir', args.repos_dir]
    if solutions_dir:
        prep += ['--solutions-dir', solutions_dir]
    if args.include_test_changes:
        prep += ['--include-test-changes']
    run(prep)

    summary = {}
    for model in models:
        mdir = path.join(args.out_dir, model)
        tests_dir = path.join(mdir, 'aug_tests')
        results = path.join(mdir, 'aug_results.json')
        os.makedirs(tests_dir, exist_ok=True)

        plog.log(f'=== model {model}: generate ===')
        run([sys.executable, path.join(HERE, 'gen_augmented.py'),
             '--csv', sel_csv, '--repos-dir', args.repos_dir,
             '--out-dir', args.out_dir, '--model', model])

        plog.log(f'=== model {model}: evaluate ===')
        run([sys.executable, path.join(HERE, 'eval_augmented.py'),
             '--csv', sel_csv, '--repos-dir', args.repos_dir,
             '--out-dir', args.out_dir, '--model', model])
        run([sys.executable, path.join(HERE, 'report_stats.py'),
             '--results', results, '--by-project'])

        n_correct = n_total = 0
        if path.isfile(results):
            with open(results) as f:
                data = json.load(f)
            n_total = len(data)
            n_correct = sum(1 for r in data.values() if r['correctly_augmented'])
        cost_usd = None
        cost_path = path.join(tests_dir, 'records', 'cost.json')
        if path.isfile(cost_path):
            with open(cost_path) as f:
                cost_usd = json.load(f).get('totals', {}).get('cost_usd')
        summary[model] = {'correct': n_correct, 'total': n_total, 'cost_usd': cost_usd}

    with open(path.join(args.out_dir, 'summary.json'), 'w') as f:
        json.dump({'k': len(selected), 'seed': args.seed,
                   'selected': selected, 'models': summary}, f, indent=2)

    plog.log('=== SUMMARY ===')
    for model, s in summary.items():
        cost = f"${s['cost_usd']:.4f}" if s['cost_usd'] is not None else 'n/a'
        plog.log(f"  {model:18} correct={s['correct']}/{s['total']}  cost={cost}")
    plog.log(f'results in {args.out_dir}/<model>/  | summary: {args.out_dir}/summary.json')


if __name__ == '__main__':
    main()
