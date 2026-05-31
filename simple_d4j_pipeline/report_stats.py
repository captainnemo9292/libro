"""Summarise augmented-test evaluation results.

Usage:
    python report_stats.py --results aug_results.json
    python report_stats.py --results aug_results.json --by-project
"""

import json
import argparse
from collections import Counter, defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', required=True)
    ap.add_argument('--by-project', action='store_true')
    args = ap.parse_args()

    with open(args.results) as f:
        results = json.load(f)

    total = len(results)
    correct = sum(1 for r in results.values() if r['correctly_augmented'])

    # Three success conditions.
    fixed_pass = sum(1 for r in results.values() if r['versions'].get('fixed') == 'pass')
    buggy_fail = sum(1 for r in results.values() if r['versions'].get('buggy') == 'fail')
    all_llm_fail = sum(
        1 for r in results.values()
        if r['llm_variants'] and all(r['versions'][v] == 'fail' for v in r['llm_variants']))

    # Per-version status counts (every distinct version name across all bugs).
    # `n/a` covers bugs that don't have that variant, so every row sums to
    # `total` (the number of bugs / augmented tests).
    per_version = defaultdict(Counter)
    all_versions = set()
    for r in results.values():
        all_versions.update(r['versions'].keys())
    for r in results.values():
        for vname in all_versions:
            if vname in r['versions']:
                per_version[vname][r['versions'][vname]] += 1
            else:
                per_version[vname]['n/a'] += 1

    pct = lambda n: f'{100 * n / total:.1f}%' if total else 'n/a'

    print(f'Bugs evaluated:        {total}')
    print(f'Correctly augmented:   {correct}  ({pct(correct)})')
    print('-' * 64)
    print(f'  pass on fixed:         {fixed_pass}  ({pct(fixed_pass)})')
    print(f'  fail on buggy:         {buggy_fail}  ({pct(buggy_fail)})')
    print(f'  fail on all buggy_llm: {all_llm_fail}  ({pct(all_llm_fail)})')
    print('-' * 64)

    # Order versions: fixed, buggy, then llm variants alphabetically.
    canonical = []
    if 'fixed' in per_version:
        canonical.append('fixed')
    if 'buggy' in per_version:
        canonical.append('buggy')
    canonical += sorted(v for v in per_version if v not in ('fixed', 'buggy'))

    statuses = ('pass', 'fail', 'compile_error', 'error', 'n/a')
    name_w = max((len(v) for v in canonical), default=12)
    header = (f'  {"version":<{name_w}}  {"total":>5}  ' +
              '  '.join(f'{s:>13}' for s in statuses))
    print(f'Per-version status (total = {total} bugs / augmented tests):')
    print(header)
    for v in canonical:
        c = per_version[v]
        cells = '  '.join(
            f'{c[s]:>5} ({100 * c[s] / total:>4.0f}%)' if total else f'{"-":>13}'
            for s in statuses)
        print(f'  {v:<{name_w}}  {total:>5}  {cells}')

    # Per-LLM patch kill rate.
    # success = test passes on fixed AND fails on buggy AND fails on THIS llm variant.
    # Denominator is total bugs evaluated (bugs without this llm's variant
    # contribute to the denominator but can never be a kill).
    all_llms = sorted({v[len('buggy_'):] if v.startswith('buggy_') else v
                       for r in results.values() for v in r['llm_variants']})
    per_llm_killed = defaultdict(int)
    for r in results.values():
        if r['versions'].get('fixed') != 'pass' or r['versions'].get('buggy') != 'fail':
            continue
        for v in r['llm_variants']:
            if r['versions'].get(v) == 'fail':
                key = v[len('buggy_'):] if v.startswith('buggy_') else v
                per_llm_killed[key] += 1

    if all_llms:
        print('-' * 64)
        print(f'Per-LLM patch kill rate '
              f'(pass fixed & fail buggy & fail this llm; total = {total} bugs):')
        lw = max(len(k) for k in all_llms)
        print(f'  {"llm":<{lw}}  {"killed/total":>14}     %')
        for key in all_llms:
            k = per_llm_killed[key]
            pctn = f'{100 * k / total:>4.0f}%' if total else 'n/a'
            print(f'  {key:<{lw}}  {k:>6}/{total:<7}  {pctn}')

    # Test-quality / distinguishability insights.
    regression_valid = [
        r for r in results.values()
        if r['versions'].get('fixed') == 'pass' and r['versions'].get('buggy') == 'fail'
    ]
    n_reg = len(regression_valid)
    n_kill_any = sum(
        1 for r in regression_valid
        if any(r['versions'].get(v) == 'fail' for v in r['llm_variants']))
    n_kill_all = sum(
        1 for r in regression_valid
        if r['llm_variants']
        and all(r['versions'].get(v) == 'fail' for v in r['llm_variants']))

    print('-' * 64)
    print('Augmented-test quality:')
    print(f'  regression-valid (pass fixed & fail buggy):    {n_reg}/{total}  ({pct(n_reg)})')
    print(f'    .. of which kill >=1 llm variant:            {n_kill_any}/{total}  ({pct(n_kill_any)})')
    print(f'    .. of which kill ALL llm variants (== correctly augmented): '
          f'{n_kill_all}/{total}  ({pct(n_kill_all)})')

    # Distribution of (#variants killed, #variants for that bug) among regression-valid tests.
    dist = Counter()
    for r in regression_valid:
        nv = len(r['llm_variants'])
        nk = sum(1 for v in r['llm_variants'] if r['versions'].get(v) == 'fail')
        dist[(nk, nv)] += 1
    if dist:
        print('  kill distribution (killed/variants -> # regression-valid bugs):')
        for (nk, nv), c in sorted(dist.items()):
            print(f'    {nk}/{nv}:  {c}')

    if args.by_project:
        per = defaultdict(lambda: [0, 0])
        for r in results.values():
            per[r['pid']][0] += 1
            per[r['pid']][1] += int(r['correctly_augmented'])
        print('-' * 48)
        print('By project (correct / total):')
        for pid in sorted(per):
            tot, ok = per[pid]
            print(f'  {pid:<18} {ok}/{tot}')


if __name__ == '__main__':
    main()
