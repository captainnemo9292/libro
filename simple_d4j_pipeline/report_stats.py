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
    per_version = defaultdict(Counter)
    for r in results.values():
        for vname, status in r['versions'].items():
            per_version[vname][status] += 1

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

    statuses = ('pass', 'fail', 'compile_error', 'error')
    name_w = max((len(v) for v in canonical), default=12)
    header = (f'  {"version":<{name_w}}  {"n":>4}  ' +
              '  '.join(f'{s:>13}' for s in statuses))
    print('Per-version status:')
    print(header)
    for v in canonical:
        c = per_version[v]
        n = sum(c.values())
        cells = '  '.join(
            f'{c[s]:>5} ({100 * c[s] / n:>4.0f}%)' if n else f'{"-":>13}'
            for s in statuses)
        print(f'  {v:<{name_w}}  {n:>4}  {cells}')

    # Per-LLM-variant success: for each LLM whose incorrect patch produced a
    # buggy_<llm>, how many bugs does the augmented test correctly distinguish
    # (pass on fixed + fail on buggy + fail on THIS variant) out of how many
    # bugs have a variant for this LLM.
    per_llm = defaultdict(lambda: [0, 0])   # llm_key -> [correct, total]
    for r in results.values():
        fixed_ok = r['versions'].get('fixed') == 'pass'
        buggy_fail = r['versions'].get('buggy') == 'fail'
        for v in r['llm_variants']:
            key = v[len('buggy_'):] if v.startswith('buggy_') else v
            per_llm[key][1] += 1
            if fixed_ok and buggy_fail and r['versions'].get(v) == 'fail':
                per_llm[key][0] += 1

    if per_llm:
        print('-' * 64)
        print('Per-LLM success (pass fixed & fail buggy & fail this variant):')
        lw = max(len(k) for k in per_llm)
        print(f'  {"llm":<{lw}}  {"correct/total":>14}     %')
        for key in sorted(per_llm):
            ok, tot = per_llm[key]
            pctn = f'{100 * ok / tot:>4.0f}%' if tot else '  n/a'
            print(f'  {key:<{lw}}  {ok:>6}/{tot:<7}  {pctn}')

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
