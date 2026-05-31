"""Tiny Java text helpers shared across the pipeline."""

import re


def matching_brace(text, i):
    """Index of the '}' matching the '{' at index `i`, skipping string/char
    literals and comments. Returns -1 if unbalanced."""
    assert text[i] == '{'
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in '"\'':
            q = c
            i += 1
            while i < n:
                if text[i] == '\\':
                    i += 2
                    continue
                if text[i] == q:
                    break
                i += 1
        elif c == '/' and i + 1 < n and text[i + 1] == '/':
            nl = text.find('\n', i)
            if nl == -1:
                return -1
            i = nl
        elif c == '/' and i + 1 < n and text[i + 1] == '*':
            end = text.find('*/', i + 2)
            if end == -1:
                return -1
            i = end + 1
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def extract_method_by_name(text, method_name):
    """Return the source of a method by name (literal/comment aware), or None."""
    for m in re.finditer(r'\b' + re.escape(method_name) + r'\s*\(', text):
        sig_start = max(text.rfind(';', 0, m.start()),
                        text.rfind('{', 0, m.start()),
                        text.rfind('}', 0, m.start())) + 1
        brace = text.find('{', m.end() - 1)
        if brace == -1:
            continue
        end = matching_brace(text, brace)
        if end == -1:
            continue
        return text[sig_start:end + 1].strip()
    return None
