"""Pluggable sources: mysql, postgres, sqlite, sqlserver, clickhouse, metabase, mongodb, plus the dump and command adapters.

    from keygraph.adapters import open_source
    src = open_source("clickhouse", run=my_select_runner)
    schema = src.schema()
"""

from __future__ import annotations


def open_source(kind: str, url: str | None = None, run=None, **kw):
    kind = kind.lower()
    if kind in ("sqlite", "sqlalchemy"):
        from .sqlite import SQLAlchemySource
        return SQLAlchemySource(url, **kw)
    if run is None and url:
        from ..probe import SelectOnlyRunner
        r = SelectOnlyRunner.from_url(url)
        run = r.run
    table = {"mysql": ("mysql", "MySQLSource"), "mariadb": ("mysql", "MySQLSource"), "postgres": ("postgres", "PostgresSource"),
             "postgresql": ("postgres", "PostgresSource"), "sqlserver": ("sqlserver", "SQLServerSource"),
             "mssql": ("sqlserver", "SQLServerSource"), "clickhouse": ("clickhouse", "ClickHouseSource")}
    if kind in table:
        import importlib
        mod, cls = table[kind]
        return getattr(importlib.import_module(f"keygraph.adapters.{mod}"), cls)(run, **kw)
    if kind == "metabase":
        from .metabase import MetabaseSource
        return MetabaseSource(**kw)
    if kind in ("mongo", "mongodb"):
        from .mongo import MongoSource
        return MongoSource(**kw)
    raise ValueError(f"unknown source kind '{kind}'")
