"""Build buggy_<llm> checkouts for every bug with an incorrect LLM patch.

For each (bug, incorrect-model) pair we copy the bug's buggy checkout and apply
the model's (production-source) patch on top, committing the result so the eval
step's `git reset --hard` returns to the patched state.

Prerequisite: checkout_all.sh has populated <repos>/<pid>_<bug>b.

Usage:
    python prepare_patched.py --csv eval.csv --repos-dir ./repos
    python prepare_patched.py --csv eval.csv --repos-dir ./repos -p Chart
    python prepare_patched.py --csv eval.csv --repos-dir ./repos --include-test-changes
"""

import os
import shutil
import argparse
import subprocess as sp
from os import path

import csv_data
import patch_utils
import plog


def export_dir(repo, prop):
    proc = sp.run(['defects4j', 'export', '-p', prop], capture_output=True, cwd=repo)
    return proc.stdout.decode('utf-8', 'ignore').strip().rstrip('/') + '/'


def git_commit_all(repo, message):
    sp.run(['git', 'add', '-A'], cwd=repo, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    sp.run(['git', '-c', 'user.email=pipeline@local', '-c', 'user.name=pipeline',
            'commit', '-m', message, '--allow-empty'],
           cwd=repo, stdout=sp.DEVNULL, stderr=sp.DEVNULL)


def build_one(repos_dir, pid, bug, llm_key, patch_text, include_tests,
              solutions_dir=None):
    buggy = path.join(repos_dir, csv_data.repo_buggy(pid, bug))
    target = path.join(repos_dir, csv_data.repo_llm(pid, bug, llm_key))

    if not path.isdir(buggy):
        return 'missing-buggy', None
    if path.isdir(target):
        return 'exists', None

    sp.run(['cp', '-a', buggy, target], check=True)
    src_dir = export_dir(target, 'dir.src.classes')
    applied, skipped, failed = patch_utils.apply_llm_patch(
        target, src_dir, patch_text, include_tests=include_tests,
        solutions_dir=solutions_dir)

    if not applied:
        # nothing applied -> not a usable variant; remove the copy
        shutil.rmtree(target, ignore_errors=True)
        return 'no-change', {'skipped': skipped, 'failed': failed}

    git_commit_all(target, f'Apply incorrect {llm_key} patch')
    return 'ok', {'applied': applied, 'skipped': skipped, 'failed': failed}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--csv', required=True, help='manual-evaluation CSV path')
    ap.add_argument('--repos-dir', required=True)
    ap.add_argument('-p', '--project', help='restrict to one project')
    ap.add_argument('-b', '--bug', help='restrict to one bug id (use with -p)')
    ap.add_argument('--include-test-changes', action='store_true',
                    help='also apply the LLM patch to test files (default: skip)')
    ap.add_argument('--solutions-dir',
                    help='on-disk path corresponding to /data/d4j_subjects/d4j_bugs; '
                         'when set, copies each LLM full solution file instead of '
                         'applying the (reformatted) diff. Diff is used as fallback.')
    args = ap.parse_args()

    bugs = csv_data.load_incorrect(args.csv)
    targets = csv_data.select(bugs, args.project, args.bug)
    n_variants = sum(len(i['incorrect']) for _, i in targets)
    plog.log(f'building buggy_<llm> checkouts: {len(targets)} bug(s), '
             f'{n_variants} variant(s)')

    counts = {'ok': 0, 'exists': 0, 'missing-buggy': 0, 'no-change': 0}
    done = 0
    for bug_id, info in targets:
        for llm_key, patch_text in info['incorrect']:
            done += 1
            tag = csv_data.repo_llm(info['pid'], info['bug'], llm_key)
            plog.log(f'[prep {done}/{n_variants}] {tag}')
            status, detail = build_one(
                args.repos_dir, info['pid'], info['bug'], llm_key,
                patch_text, args.include_test_changes, args.solutions_dir)
            counts[status] = counts.get(status, 0) + 1
            if status == 'ok':
                plog.log(f'    OK: applied {detail["applied"]}'
                         + (f'  skipped_tests={len(detail["skipped"])}' if detail['skipped'] else '')
                         + (f'  FAILED={detail["failed"]}' if detail['failed'] else ''))
            elif status == 'no-change':
                plog.log(f'    warn: nothing applied (failed={detail["failed"]})')
            elif status == 'missing-buggy':
                plog.log(f'    skip: buggy checkout missing')
            elif status == 'exists':
                plog.log(f'    skip: already built')
    plog.log(f'[done] {counts}')


if __name__ == '__main__':
    main()
