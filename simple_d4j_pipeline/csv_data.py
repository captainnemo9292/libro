"""Read the manual-evaluation CSV and expose the bugs with incorrect LLM patches.

We only look at the two "-ori" model columns the task cares about. A patch
counts as a usable incorrect patch when its Eval cell == 'incorrect' AND the
patch text is non-empty.
"""

import csv

# (llm_key, patch_column_index, eval_column_index) for the two -ori models.
LLM_COLUMNS = [
    ('claude-sonnet-4', 4, 5),
    ('gemini-3-pro-preview', 10, 11),
]

DEV_PATCH_COL = 2
BUG_ID_COL = 1


def split_bug(bug_id):
    """'Chart-16' -> ('Chart', '16'); 'JacksonDatabind-1' -> ('JacksonDatabind', '1')."""
    pid, num = bug_id.rsplit('-', 1)
    return pid, num


def repo_buggy(pid, bug):
    return f'{pid}_{bug}b'


def repo_fixed(pid, bug):
    return f'{pid}_{bug}f'


def repo_llm(pid, bug, llm_key):
    return f'{pid}_{bug}_{llm_key}'


def select(bugs, project=None, bug=None):
    """Filter a load_incorrect() dict by project and/or bug id, returning a
    sorted list of (bug_id, info). `bug` is the numeric d4j id (e.g. '16')."""
    out = []
    for bug_id, info in sorted(bugs.items()):
        if project and info['pid'] != project:
            continue
        if bug and info['bug'] != str(bug):
            continue
        out.append((bug_id, info))
    return out


def load_incorrect(csv_path):
    """Return a dict keyed by bug_id for every bug with >=1 usable incorrect
    patch:

        {
          'Chart-16': {
             'pid': 'Chart', 'bug': '16',
             'dev_patch': '<diff>',
             'incorrect': [('claude-sonnet-4', '<diff>'), ...],
          }, ...
        }
    """
    out = {}
    with open(csv_path, newline='') as f:
        rows = list(csv.reader(f))
    for row in rows[1:]:
        if len(row) <= BUG_ID_COL or not row[BUG_ID_COL]:
            continue
        bug_id = row[BUG_ID_COL]
        incorrect = []
        for key, patch_idx, eval_idx in LLM_COLUMNS:
            if len(row) <= eval_idx:
                continue
            if row[eval_idx].strip().lower() == 'incorrect' and row[patch_idx].strip():
                incorrect.append((key, row[patch_idx]))
        if not incorrect:
            continue
        pid, bug = split_bug(bug_id)
        out[bug_id] = {
            'pid': pid, 'bug': bug,
            'dev_patch': row[DEV_PATCH_COL],
            'incorrect': incorrect,
        }
    return out
