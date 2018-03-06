import os
import shutil
import subprocess
from contextlib import contextmanager
from fnmatch import fnmatch
from pathlib import Path

import yaml
from anytree import ChildResolverError
from anytree import PreOrderIter
from anytree import Resolver
from nose.tools import assert_in, eq_, ok_
from nose.tools import assert_is_instance
from nose.tools import assert_not_in
from nose.tools import assert_raises

from runs import db
from runs import main
from runs.cfg import Cfg
from runs.db import read
from runs.pattern import Pattern
from runs.run import Run
from runs.util import NAME, cmd

# TODO: sad path

CHILDREN = 'children'
EXE = """\
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--option', default=0)
print(vars(parser.parse_args()))\
"""
COMMAND = 'python test.py'
WORK_DIR = '/tmp/test-run-manager'
DB_PATH = Path(WORK_DIR, 'runs.yml')
ROOT = '.runs'
DESCRIPTION = 'test new command'
SEP = '/'
SUBDIR = 'subdir'
TEST_RUN = 'test_run'
DEFAULT_CFG = Cfg(root=ROOT, db_path=DB_PATH, quiet=True)


def sessions():
    try:
        output = cmd('tmux list-session -F "#{session_name}"'.split(), fail_ok=True)
        assert isinstance(output, str)
        return output.split('\n')
    except subprocess.CalledProcessError:
        return []


def quote(string):
    return '"' + string + '"'


def get_name(nodes, name):
    return next(n for n in nodes if n[NAME] == name)


class ParamGenerator:
    def __init__(self):
        self.paths = [TEST_RUN]
        self.dir_names = [[], ['checkpoints', 'tensorboard']]
        self.flags = [[], ['--option=1']]

    def __iter__(self):
        for path in self.paths:
            for dir_names in self.dir_names:
                for flags in self.flags:
                    yield path, dir_names, flags

    def __next__(self):
        return next(iter(self))


class SimpleParamGenerator(ParamGenerator):
    def __init__(self):
        super().__init__()
        self.paths = [TEST_RUN]
        self.dir_names = [[]]
        self.flags = [[]]


class ParamGeneratorWithSubdir:
    def __init__(self):
        super().__init__()
        self.paths = [SUBDIR + SEP + TEST_RUN]


class ParamGeneratorWithPatterns(ParamGenerator):
    def __init__(self):
        super().__init__()
        self.paths += ['*', 'subdir/*', 'test*']


def db_entry(path):
    if not path:
        with DB_PATH.open() as f:
            return yaml.load(f)
    *path, name = path.split(SEP)
    entry = db_entry(SEP.join(path))
    assert_in(CHILDREN, entry)
    return get_name(entry[CHILDREN], name)


# TODO what if config doesn't have required fields?

@contextmanager
def _setup(path, dir_names=None, flags=None):
    if dir_names is None:
        dir_names = []
    if flags is None:
        flags = []
    assert isinstance(path, str)
    assert isinstance(dir_names, list)
    assert isinstance(flags, list)
    Path(WORK_DIR).mkdir(exist_ok=True)
    os.chdir(WORK_DIR)
    if any([dir_names, flags]):
        with Path(WORK_DIR, '.runsrc').open('w') as f:
            f.write(
                """\
[filesystem]
root = {}
db_path = runs.yml
dir_names = {}

[flags]
{}\
""".format(ROOT, ' '.join(dir_names), '\n'.join(flags)))
    cmd(['git', 'init', '-q'], cwd=WORK_DIR)
    with Path(WORK_DIR, '.gitignore').open('w') as f:
        f.write('.runsrc')
    with Path(WORK_DIR, 'test.py').open('w') as f:
        f.write(EXE)
    cmd(['git', 'add', '--all'], cwd=WORK_DIR)
    cmd(['git', 'commit', '-am', 'init'], cwd=WORK_DIR)
    main.main(['-q', 'new', path, COMMAND, "--description=" + DESCRIPTION])
    yield
    cmd('tmux kill-session -t'.split() + [path], fail_ok=True)
    shutil.rmtree(WORK_DIR)


def check_tmux_new(path):
    assert_in(quote(path), sessions())


def check_db_new(path, flags):
    entry = db_entry(path)

    # check values that should probably be mocks
    for key in ['commit', 'datetime']:
        assert_in(key, entry)

    # check known values
    name = path.split(SEP)[-1]
    attrs = dict(description=DESCRIPTION,
                 input_command=COMMAND,
                 name=name)
    for key, attr in attrs.items():
        assert_in(key, entry)
        eq_(entry[key], attr)
    for flag in flags:
        assert_in(flag, entry['full_command'])


def check_files_new(path, dir_names):
    for dir_name in dir_names:
        path = Path(WORK_DIR, ROOT, dir_name, path)
        ok_(path.exists(), msg="{} does not exist.".format(path))


def check_tmux_rm(path):
    assert_not_in(quote(path), sessions())


def check_db_rm(path):
    with assert_raises(ChildResolverError):
        Resolver().glob(read(DB_PATH), path)


def check_files_rm(path):
    for root, dirs, files in os.walk(WORK_DIR):
        for filename in files:
            ok_(not fnmatch(filename, path))


def test_new():
    for path, dir_names, flags in ParamGenerator():
        with _setup(path, dir_names, flags):
            yield check_tmux_new, path
            yield check_db_new, path, flags
            yield check_files_new, path, dir_names


def test_rm():
    for path, dir_names, flags in ParamGenerator():
        with _setup(path, dir_names, flags):
            main.main(['-q', 'rm', '-y', path])
            yield check_tmux_rm, path
            yield check_db_rm, path
            yield check_files_rm, path

            # TODO: patterns


def check_list_happy(pattern):
    string = Pattern(pattern).tree_string(print_attrs=False)
    eq_(string, """\
.
└── test_run
""")


def check_list_sad(pattern):
    with assert_raises(SystemExit):
        Pattern(pattern, cfg=DEFAULT_CFG).tree_string()


def test_list():
    path = TEST_RUN
    for _, dir_names, flags in ParamGenerator():
        with _setup(path, dir_names, flags):
            for pattern in ['*', 'test*']:
                yield check_list_happy, pattern
            for pattern in ['x*', 'test']:
                yield check_list_sad, pattern


def check_table(table):
    assert_is_instance(table, str)
    for member in [COMMAND, DESCRIPTION, TEST_RUN]:
        assert_in(member, table)


def test_table():
    with _setup(TEST_RUN):
        yield check_table, Pattern('*').table(100)
        yield check_table, db.table(PreOrderIter(db.read(DB_PATH)), [], 100)


def test_lookup():
    with _setup(TEST_RUN):
        pattern = Pattern('*', cfg=DEFAULT_CFG)
        for key, value in dict(name=TEST_RUN,
                               description=DESCRIPTION,
                               input_command=COMMAND).items():
            eq_(pattern.lookup(key), [value])
        with assert_raises(SystemExit):
            pattern.lookup('x')


def test_chdesc():
    with _setup(TEST_RUN):
        description = 'new description'
        main.main(['chdesc', TEST_RUN, '--description=' + description])
        eq_(Run(TEST_RUN).lookup('description'), description)
