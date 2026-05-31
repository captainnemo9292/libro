"""Inject a bare LLM-generated JUnit test method into a Defects4J checkout.

Ported and simplified from libro's `common.py`. Given a single generated test
*method* (e.g. ``public void testX() { ... }``) it:

  1. resolves the imports the method needs (project classes + JUnit asserts),
  2. finds the existing test class most token-similar to the method,
  3. injects the method (renamed ``<name>AutoGen``) plus any missing imports.

`inject()` returns the ``Class::method`` identifier to pass to
``defects4j test -t``. The renamed method always contains the substring
``AutoGen`` so the runner can recognise it in the failing-test list.

Requires the `javalang` package (`pip install javalang`).
"""

import os
import re
import codecs
import subprocess as sp
from os import path
from collections import Counter, defaultdict

import javalang


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def enforce_static_assertions(gen_test):
    """Rewrite `Assert.fail`/`Assert.assertX` to the static-import form, so the
    test compiles inside classes that statically import org.junit.Assert.*."""
    if 'Assert.' in gen_test:
        gen_test = gen_test.replace('Assert.fail', 'fail')
        gen_test = gen_test.replace('Assert.assert', 'assert')
    return gen_test


def parse_method(gen_test):
    tokens = javalang.tokenizer.tokenize(gen_test)
    parser = javalang.parser.Parser(tokens)
    return parser.parse_member_declaration()


def get_most_common_item(iterator):
    counts = Counter(iterator)
    return max(counts.keys(), key=counts.__getitem__)


# --------------------------------------------------------------------------- #
# Import resolution
# --------------------------------------------------------------------------- #
def resolve_imports(repo_path, src_dir, gen_test):
    """Return (classpaths, asserts) needed by the test method.

    `classpaths` are fully-qualified project classes referenced by the method;
    `asserts` are JUnit assertion method names used (assert*, fail).
    `src_dir` is the project source dir relative to repo_path, WITH a trailing
    slash (e.g. 'src/main/java/').
    """
    tree = parse_method(gen_test)

    RelevantTypeClass = (javalang.tree.VariableDeclaration,
                         javalang.tree.MemberReference,
                         javalang.tree.MethodInvocation,
                         javalang.tree.ReferenceType,
                         javalang.tree.ClassCreator,)
    ExceptionTypeClass = (javalang.tree.CatchClauseParameter,
                          javalang.tree.MethodDeclaration,)

    needed_class_stubs = set()
    needed_asserts = set()
    for _, node in tree:
        if isinstance(node, ExceptionTypeClass):
            exception_types = None
            if isinstance(node, javalang.tree.CatchClauseParameter):
                exception_types = node.types
            elif isinstance(node, javalang.tree.MethodDeclaration):
                exception_types = node.throws
            if exception_types:
                for exception_type in exception_types:
                    needed_class_stubs.add(exception_type)
        if isinstance(node, RelevantTypeClass):
            if isinstance(node, javalang.tree.VariableDeclaration):
                needed_class_stubs.add(node.type.name)
            elif isinstance(node, javalang.tree.ReferenceType):
                needed_class_stubs.add(node.name)
            elif isinstance(node, javalang.tree.ClassCreator):
                needed_class_stubs.add(node.type.name)
            elif (isinstance(node, javalang.tree.MethodInvocation) and
                  ('assert' in node.member or node.member == 'fail')):
                needed_asserts.add(node.member)
            elif isinstance(node, javalang.tree.MemberReference):
                if (node.qualifier and len(node.qualifier) > 0 and
                        node.qualifier[0].isupper()):
                    needed_class_stubs.add(node.qualifier.split('.')[0])

    classpaths = []
    for class_stub in needed_class_stubs:
        cp = sp.run(['find', src_dir, '-name', f'{class_stub}.java'],
                    capture_output=True, cwd=repo_path)
        out_lines = cp.stdout.decode('utf-8', 'ignore').split('\n')
        if len(out_lines) != 2:
            # 0 or >1 source files for this name: fall back to existing imports.
            cp = sp.run(['grep', '-rh', r'import.*\.' + class_stub + ';', '.'],
                        capture_output=True, cwd=repo_path)
            example_paths = cp.stdout.decode('utf-8', 'ignore').split('\n')
            example_paths = [e.removeprefix('import ').rstrip('\r').rstrip(';')
                             for e in example_paths]
            example_paths = [e for e in example_paths if e]
            if len(example_paths) == 0:
                if len(out_lines) == 1:
                    continue  # not a project class (likely JDK / dependency)
                filepath = out_lines[0]  # multiple matches, none imported: pick one
                classpath = filepath.removeprefix(src_dir).removesuffix('.java')
                classpath = classpath.replace('/', '.')
            else:
                classpath = get_most_common_item(example_paths)
        else:
            filepath = out_lines[0]
            classpath = filepath.removeprefix(src_dir).removesuffix('.java')
            classpath = classpath.replace('/', '.')
        classpaths.append(classpath.strip('.'))
    return classpaths, needed_asserts


# --------------------------------------------------------------------------- #
# Choosing the host test class
# --------------------------------------------------------------------------- #
def _is_injectable_test_class(file_content, filepath, titular_class_name):
    """Abstract classes and @RunWith(Parameterized) classes cannot host a
    plain injected method."""
    tree = javalang.parse.parse(file_content)
    titular_class_def = None
    for _, node in tree.filter(javalang.tree.ClassDeclaration):
        if node.name == titular_class_name:
            titular_class_def = node
            break
    if titular_class_def is None:
        return False
    if 'abstract' in titular_class_def.modifiers:
        return False
    for annotation in titular_class_def.annotations:
        if hasattr(annotation, 'element'):
            anno = annotation.element
            if hasattr(anno, 'type') and anno.type.name == 'Parameterized':
                return False
    return True


def find_best_test_class(repo_path, test_dir, gen_test):
    """Pick the existing test .java file whose token set best matches the
    generated method. Returns (absolute_path, repo_relative_path)."""
    file_scores = defaultdict(float)
    test_tokens = set(e.value for e in javalang.tokenizer.tokenize(gen_test))

    for root, _dirs, files in os.walk(path.join(repo_path, test_dir), topdown=False):
        for name in [e for e in files if e.endswith('.java')]:
            filepath = path.join(root, name)
            with codecs.open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                file_cont = f.read()
            if 'abstract' in file_cont or '@RunWith(Parameterized.class)' in file_cont:
                if not _is_injectable_test_class(file_cont, filepath, name.removesuffix('.java')):
                    continue
            if '@Ignore' in file_cont:
                continue
            file_tokens = set(e.value for e in javalang.tokenizer.tokenize(file_cont))
            file_scores[filepath.removeprefix(repo_path)] += \
                len(test_tokens & file_tokens) / len(test_tokens)

    if not file_scores:
        raise RuntimeError(f'No injectable test class found under {test_dir}')

    best_file = sorted(file_scores.keys(),
                       key=lambda x: (file_scores[x], x), reverse=True)[0]
    if best_file.startswith('/'):
        best_file = best_file[1:]
    return path.join(repo_path, best_file), best_file


# --------------------------------------------------------------------------- #
# Import bookkeeping + actual injection
# --------------------------------------------------------------------------- #
def _derive_unhandled_imports(test_class_content, needed_classpaths, needed_class_stubs):
    existing_imports = re.findall(r'import (.*);', test_class_content)

    already_imported_stubs = []
    for stub in needed_class_stubs:
        for imp in existing_imports:
            if imp.endswith(stub):
                already_imported_stubs.append(stub)
                break

    unhandled_classpaths = []
    for ncp in needed_classpaths:
        if not any(ncp.endswith(stub) for stub in already_imported_stubs):
            unhandled_classpaths.append(ncp)

    unhandled_imports = []
    for ncp in unhandled_classpaths:
        ncp_package = '.'.join(ncp.split('.')[:-1])
        search_terms = [
            f'package {ncp_package};',
            f'import {ncp};',
            f'import static {ncp};',
            f'import {ncp_package}.*;',
            f'import static {ncp_package}.*;',
        ]
        if not any(term in test_class_content for term in search_terms):
            unhandled_imports.append(ncp)
    return unhandled_imports


def _derive_unhandled_assert_imports(test_class_content, needed_asserts):
    unhandled_imports = []
    for na in needed_asserts:
        ncp_form = f'static org.junit.Assert.{na}'
        if f'import {ncp_form};' not in test_class_content:
            unhandled_imports.append(ncp_form)
    return unhandled_imports


def _inject_with_imports(best_classpath, testf_lines, gen_test, unhandled_imports):
    new_test_lines = testf_lines[:]

    # Insert imports right after the package / before the first import.
    import_loc = -1
    for idx, line in enumerate(testf_lines):
        if 'package' in line:
            import_loc = idx + 1
        if 'import' in line:
            import_loc = idx
            break
    assert import_loc != -1, best_classpath
    new_test_lines = (
        new_test_lines[:import_loc] +
        [f'import {ncp};\n' for ncp in unhandled_imports] +
        new_test_lines[import_loc:]
    )

    # Rename the method to avoid collisions and tag it with AutoGen.
    org_test_name = parse_method(gen_test).name
    new_test_name = org_test_name + 'AutoGen'
    gen_test = gen_test.replace('void ' + org_test_name, 'void ' + new_test_name)
    if '@Test' in ''.join(testf_lines):
        gen_test = '@Test\n' + gen_test.strip()

    # Find the closing brace of the titular class and insert before it.
    tree = javalang.parse.parse(''.join(new_test_lines))
    is_titular_class = [(e.name == best_classpath.split('.')[-1]) for e in tree.types]
    assert sum(is_titular_class) == 1, \
        f'Class matching classpath {best_classpath} not found.'
    titular_class_idx = is_titular_class.index(True)
    if titular_class_idx + 1 == len(tree.types):
        start_loc = len(new_test_lines) - 1
    else:
        start_loc = tree.types[titular_class_idx + 1]._position.line - 1

    final_paren_loc = 0
    for idx in range(start_loc, 0, -1):
        if '}' in new_test_lines[idx]:
            final_paren_loc = idx
            break
    assert final_paren_loc != 0
    new_test_lines = (
        new_test_lines[:final_paren_loc] +
        [e + '\n' for e in gen_test.split('\n')] +
        new_test_lines[final_paren_loc:]
    )
    return ''.join(new_test_lines), gen_test


def inject(repo_path, src_dir, test_dir, gen_test):
    """Inject `gen_test` into the best host test class of the checkout at
    `repo_path`. Mutates that .java file on disk and returns the
    ``Class::method`` name for `defects4j test -t`.

    `src_dir` / `test_dir` are repo-relative dirs WITH trailing slash
    (as returned by `defects4j export -p dir.src.classes / dir.src.tests`).
    """
    classpaths, asserts = resolve_imports(repo_path, src_dir, gen_test)
    best_path, best_file = find_best_test_class(repo_path, test_dir, gen_test)

    with codecs.open(best_path, 'r', encoding='utf-8', errors='ignore') as f:
        testf_lines = f.readlines()
    testf_content = ''.join(testf_lines)
    needs_assert_imports = '@Test' in testf_content

    class_stubs = [e.split('.')[-1] for e in classpaths]
    unhandled = _derive_unhandled_imports(testf_content, classpaths, class_stubs)
    if needs_assert_imports:
        unhandled.extend(_derive_unhandled_assert_imports(testf_content, asserts))

    best_classpath = best_file.removeprefix(test_dir).removesuffix('.java')
    best_classpath = best_classpath.replace('/', '.').strip('.')

    new_content, new_gen_test = _inject_with_imports(
        best_classpath, testf_lines, gen_test, unhandled)

    with open(best_path, 'w') as f:
        f.write(new_content)

    return f'{best_classpath}::' + parse_method(new_gen_test).name
