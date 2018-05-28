import os
import shutil
import sqlite3
from collections import namedtuple
from contextlib import contextmanager
from pathlib import Path, PurePath
from typing import List, Tuple, Union

from runs.util import _exit, get_permission
from tabulate import tabulate


@contextmanager
def open_table(path):
    conn = sqlite3.connect(path)
    yield Table(cursor=conn.cursor())
    conn.commit()
    conn.close()


class RunEntry(
    namedtuple('RunEntry', [
        'path', 'full_command', 'commit', 'datetime', 'description',
        'input_command'
    ])):
    __slots__ = ()

    def __str__(self):
        return ','.join([f"'{x}'" for x in self])

    def replace(self, **kwargs):
        return super()._replace(**kwargs)

    @staticmethod
    def fields() -> Tuple[str]:
        return RunEntry(*RunEntry._fields)

    def asdict(self) -> dict:
        return self._asdict()


PathLike = Union[str, PurePath, Path]


class Conn:
    def  __init__(self, conn):
        self.conn = conn

    def execute(self, x):
        print(x)
        return self.conn.execute(x)

    def commit(self):
        return self.conn.commit()

    def close(self):
        return self.conn.close()


class Table:
    def __init__(self, path):
        self.path = path
        self.table_name = 'runs'
        self.conn = None
        self.columns = set(RunEntry.fields())
        self.key = 'path'
        self.fields = RunEntry.fields()

    def __enter__(self):
        self.conn = sqlite3.connect(str(self.path))
        fields = [f"'{f}' text NOT NULL" for f in self.fields]
        fields[0] += ' PRIMARY KEY'
        self.conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {self.table_name} ({', '.join(fields)})
        """)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.conn.commit()
        self.conn.close()

    def condition(self, pattern) -> str:
        return "FROM {} WHERE {} LIKE '{}'".format(self.table_name, self.key,
                                                   pattern)

    def __contains__(self, pattern: PathLike) -> bool:
        return bool(
            self.conn.execute(f"""
        SELECT COUNT(*) {self.condition(pattern)}
        """).fetchone()[0])

    def __iadd__(self, run: RunEntry) -> None:
        self.conn.execute(f"""
        INSERT INTO {self.table_name} ({self.fields}) VALUES ({run})
        """)

    def update(self, pattern: PathLike, **kwargs):
        updates = ','.join(f"'{k}' = '{v}'" for k, v in kwargs.items())
        self.conn.execute(f"""
        UPDATE {self.table_name} SET {updates} WHERE {self.key} LIKE '{pattern}'
        """)

    def __getitem__(self, pattern: PathLike) -> List[RunEntry]:
        return [
            RunEntry(*e) for e in self.conn.execute(f"""
        SELECT * {self.condition(pattern)}
        """).fetchall()
            ]

    def __delitem__(self, pattern: PathLike):
        self.conn.execute(f"""
        DELETE {self.condition(pattern)}
        """)

    def delete(self):
        self.conn.execute(f"""
        DROP TABLE IF EXISTS {self.table_name}
        """)


def tree_string(tree, print_attrs=True):
    raise NotImplemented
    string = ''
    for pre, fill, node in RenderTree(tree):
        public_attrs = {
            k: v
            for k, v in vars(node).items()
            if not k.startswith('_') and not k == 'name'
            }
        if public_attrs:
            pass
            # pnode = yaml.dump(
            #     public_attrs, default_flow_style=False).split('\n')
        else:
            pnode = ''
        string += "{}{}\n".format(pre, node.name)
        if print_attrs:
            for line in pnode:
                string += "{}{}\n".format(fill, line)
    return string


def table(runs, hidden_columns, column_width):
    assert isinstance(column_width, int)

    def get_values(run, key):
        try:
            value = str(getattr(run.node(), key))
            if len(value) > column_width:
                value = value[:column_width] + '...'
            return value
        except AttributeError:
            return '_'

    keys = set([
                   key for run in runs for key in vars(run.node())
                   if not key.startswith('_')
                   ])
    headers = sorted(set(keys) - set(hidden_columns))
    table = [[run.path] + [get_values(run, key) for key in headers]
             for run in sorted(runs, key=lambda r: r.path)]
    return tabulate(table, headers=headers)


def killall(db_path, root):
    if db_path.exists():
        if get_permission("Curent runs:\n{}\nDestroy all?".format(
                tree_string(db_path=db_path))):
            db_path.unlink()
    shutil.rmtree(root, ignore_errors=True)


def no_match(pattern, tree=None, db_path=None):
    _exit(f'No runs match pattern "{pattern}". Recorded runs:\n'
          f'{tree_string(tree, db_path)}')
