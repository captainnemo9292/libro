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

    # Per-version status tallies, plus the three success conditions.
    fixed_pass = sum(1 for r in results.values() if r['versions'].get('fixed') == 'pass')
    buggy_fail = sum(1 for r in results.values() if r['versions'].get('buggy') == 'fail')
    all_llm_fail = sum(
        1 for r in results.values()
        if r['llm_variants'] and all(r['versions'][v] == 'fail' for v in r['llm_variants']))

    fixed_status = Counter(r['versions'].get('fixed') for r in results.values())
    buggy_status = Counter(r['versions'].get('buggy') for r in results.values())
    llm_status = Counter(
        r['versions'][v] for r in results.values() for v in r['llm_variants'])

    pct = lambda n: f'{100 * n / total:.1f}%' if total else 'n/a'

    print(f'Bugs evaluated:        {total}')
    print(f'Correctly augmented:   {correct}  ({pct(correct)})')
    print('-' * 48)
    print(f'  pass on fixed:       {fixed_pass}  ({pct(fixed_pass)})')
    print(f'  fail on buggy:       {buggy_fail}  ({pct(buggy_fail)})')
    print(f'  fail on all buggy_llm: {all_llm_fail}  ({pct(all_llm_fail)})')
    print('-' * 48)
    print(f'  fixed status:        {dict(fixed_status)}')
    print(f'  buggy status:        {dict(buggy_status)}')
    print(f'  buggy_llm status:    {dict(llm_status)}  '
          f'({sum(llm_status.values())} variants)')

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
