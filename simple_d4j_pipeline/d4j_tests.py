"""Extract the bug-triggering developer tests from a Defects4J checkout.

`defects4j export -p tests.trigger` lists the triggering tests of the currently
checked-out bug as `Class::method` lines. We read the source of each such
method so it can be embedded in the LLM prompt.
"""

import codecs
import subprocess as sp
from os import path

from java_utils import extract_method_by_name as extract_method


def trigger_tests(repo):
    """Return the list of triggering 'Class::method' ids for the checkout."""
    proc = sp.run(['defects4j', 'export', '-p', 'tests.trigger'],
                  capture_output=True, cwd=repo)
    out = proc.stdout.decode('utf-8', 'ignore').strip()
    ids = []
    for line in out.replace(';', '\n').split('\n'):
        line = line.strip()
        if '::' in line:
            ids.append(line)
    return ids


def _export_dir(repo, prop):
    proc = sp.run(['defects4j', 'export', '-p', prop], capture_output=True, cwd=repo)
    return proc.stdout.decode('utf-8', 'ignore').strip().rstrip('/') + '/'


def collect_trigger_sources(repo):
    """Return concatenated source of all triggering test methods in the checkout."""
    test_dir = _export_dir(repo, 'dir.src.tests')
    blocks = []
    for tid in trigger_tests(repo):
        cls, _, method = tid.partition('::')
        jfile = path.join(repo, test_dir, cls.replace('.', '/') + '.java')
        if not path.isfile(jfile):
            blocks.append(f'// {tid} (source file not found)')
            continue
        with codecs.open(jfile, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        src = extract_method(content, method)
        blocks.append(f'// {tid}\n{src}' if src
                      else f'// {tid} (method body not found)')
    return '\n\n'.join(blocks)
