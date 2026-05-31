"""Preprocess and apply the LLM-generated patches stored in the evaluation CSV.

The patches in the CSV are `diff -r` outputs between absolute paths on the
machine that generated them, e.g.

    --- /data/.../result.bak/restore/source/src/main/java/org/jfree/.../X.java
    +++ /data/.../source_ori-claude-sonnet-4/src/main/java/org/jfree/.../X.java

while a Defects4J checkout stores the same class under the project's real
source root (`source/...` for Chart, `src/...` for Closure, ...). We therefore
reduce every header path to its *package path* (`org/jfree/.../X.java`), locate
the matching file inside the checkout, and apply the hunks there with GNU
`patch` (whitespace-insensitive, fuzzy) so layout differences don't matter.
"""

import os
import re
import shutil
import tempfile
import subprocess as sp
from os import path


def _clean_header_path(s):
    """Strip the trailing diff timestamp (tab- or space-separated)."""
    s = s.split('\t')[0]
    s = re.sub(r'\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}.*$', '', s)
    return s.strip()


# Source roots, longest first; used to strip a path down to its package path.
_SOURCE_ROOTS = [
    'src/main/java/', 'src/test/java/', 'src/java/', 'src/test/',
    'source/', 'tests/', 'test/', 'src/',
]


def package_path(p):
    """Reduce a diff header path to the path relative to its source root,
    e.g. '.../src/main/java/org/jfree/X.java' -> 'org/jfree/X.java'."""
    best_end = None
    for root in _SOURCE_ROOTS:
        i = p.rfind(root)
        if i != -1:
            end = i + len(root)
            if best_end is None or end > best_end:
                best_end = end
    return p[best_end:] if best_end is not None else p.split('/')[-1]


def is_test_path(p):
    """True if the header path points at a test source file."""
    if '/src/test/' in p or '/test/' in p or '/tests/' in p:
        return True
    base = p.rstrip('/').split('/')[-1]
    return base.endswith('Test.java') or base.endswith('Tests.java')


def split_sections(patch_text):
    """Split a (possibly multi-file) `diff -r` blob into per-file sections.

    Returns a list of dicts: {'from', 'to', 'hunks': [lines]}.
    `Only in ...` directory-listing lines are dropped.
    """
    lines = patch_text.splitlines()
    sections = []
    cur = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith('Only in '):
            i += 1
            continue
        # A file header is a '--- ' line immediately followed by a '+++ ' line.
        if line.startswith('--- ') and i + 1 < n and lines[i + 1].startswith('+++ '):
            if cur is not None:
                sections.append(cur)
            cur = {
                'from': _clean_header_path(line[4:]),
                'to': _clean_header_path(lines[i + 1][4:]),
                'hunks': [],
            }
            i += 2
            continue
        if line.startswith('diff '):
            i += 1
            continue
        if cur is not None:
            cur['hunks'].append(line)
        i += 1
    if cur is not None:
        sections.append(cur)
    # keep only sections that actually carry hunks
    return [s for s in sections if any(h.startswith('@@') for h in s['hunks'])]


def locate_file(repo, src_dir, pkg_path):
    """Find the checkout file whose path ends with `pkg_path`.

    Prefers an exact `<src_dir>/<pkg_path>` match; otherwise searches the repo.
    Returns the repo-relative path, or None.
    """
    direct = path.join(src_dir.rstrip('/'), pkg_path)
    if path.isfile(path.join(repo, direct)):
        return direct
    cp = sp.run(['find', '.', '-path', f'*/{pkg_path}'],
                capture_output=True, cwd=repo)
    matches = [m for m in cp.stdout.decode('utf-8', 'ignore').split('\n') if m]
    if not matches:
        return None
    matches.sort(key=len)
    return matches[0].removeprefix('./')


def _apply_hunks(repo, target_rel, hunks):
    """Apply `hunks` to `target_rel` (relative to repo) using GNU patch."""
    body = '\n'.join([f'--- {target_rel}', f'+++ {target_rel}'] + hunks) + '\n'
    with tempfile.NamedTemporaryFile('w', suffix='.patch', delete=False) as tf:
        tf.write(body)
        patch_file = tf.name
    try:
        proc = sp.run(
            ['patch', '-p0', '-l', '--fuzz=3', '--no-backup-if-mismatch',
             '-d', repo, '-i', patch_file],
            capture_output=True)
        return proc.returncode == 0, proc.stdout.decode('utf-8', 'ignore') + \
            proc.stderr.decode('utf-8', 'ignore')
    finally:
        os.unlink(patch_file)


def solution_rel_path(header):
    """From a '+++' header like
    /data/d4j_subjects/d4j_bugs/Chart-16/source_ori-claude-sonnet-4/src/main/java/org/.../X.java
    return the part after '/d4j_bugs/' (i.e. the path under the solutions dir),
    or None."""
    marker = '/d4j_bugs/'
    i = header.find(marker)
    if i == -1:
        return None
    return header[i + len(marker):]


def apply_llm_patch(repo, src_dir, patch_text, include_tests=False,
                    solutions_dir=None):
    """Reproduce the LLM's changes on a checkout.

    If `solutions_dir` is given (the on-disk path corresponding to
    /data/d4j_subjects/d4j_bugs), each changed production file is replaced
    wholesale with the LLM's full solution file -- robust against the
    reformatting in the CSV diffs. Falls back to applying the diff hunks when
    the full file isn't found.

    Returns (applied, skipped, failed). `applied` entries note the method, e.g.
    'src/.../X.java (full-file)'.
    """
    applied, skipped, failed = [], [], []
    for sec in split_sections(patch_text):
        header = sec['to'] or sec['from']
        if is_test_path(header) and not include_tests:
            skipped.append(header)
            continue
        pkg = package_path(header)
        target = locate_file(repo, src_dir, pkg)
        if target is None:
            failed.append((pkg, 'file not found in checkout'))
            continue

        # Preferred: copy the LLM's full solution file over the checkout file.
        if solutions_dir:
            rel = solution_rel_path(header)
            src_file = path.join(solutions_dir, rel) if rel else None
            if src_file and path.isfile(src_file):
                shutil.copyfile(src_file, path.join(repo, target))
                applied.append(f'{target} (full-file)')
                continue

        # Fallback: apply the diff hunks.
        ok, msg = _apply_hunks(repo, target, sec['hunks'])
        if ok:
            applied.append(target)
        else:
            failed.append((target, msg.strip().splitlines()[-1] if msg.strip() else 'patch failed'))
    return applied, skipped, failed
