"""Minimal timestamped logging shared across the pipeline scripts."""

import sys
import time


def log(*args):
    """Print a timestamped, flushed line."""
    ts = time.strftime('%H:%M:%S')
    print(f'[{ts}]', *args, flush=True)


def block(title, content):
    """Print a labelled, delimited multi-line block (e.g. a test method)."""
    bar = '-' * 64
    print(f'    {title}:')
    print(f'    {bar}')
    for line in (content or '<empty>').splitlines() or ['<empty>']:
        print(f'    | {line}')
    print(f'    {bar}', flush=True)
