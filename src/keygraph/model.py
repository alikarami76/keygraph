"""Normalised schema model shared by every adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Column:
    name: str
    type: str = ""
    nullable: bool = True
    position: int = 0
    key: str = ""  # PRI / UNI / MUL as reported by MySQL COLUMNS, or ""


@dataclass
class Index:
    name: str
    columns: list[str]
    unique: bool = False
    primary: bool = False
    approximate: bool = False  # derived from COLUMN_KEY only; composition unknown


@dataclass
class ForeignKey:
    name: str
    columns: list[str]
    ref_schema: str
    ref_table: str
    ref_columns: list[str]


@dataclass
class Table:
    schema: str
    name: str
    columns: list[Column] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def primary_key(self) -> list[str]:
        for ix in self.indexes:
            if ix.primary:
                return list(ix.columns)
        return []

    @property
    def unique_keys(self) -> list[list[str]]:
        return [list(ix.columns) for ix in self.indexes if ix.unique and not ix.primary]

    def column(self, name: str) -> Column | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None


@dataclass
class Schema:
    tables: dict[str, Table] = field(default_factory=dict)

    def add(self, table: Table) -> None:
        self.tables[table.id] = table

    def get_or_create(self, schema: str, name: str) -> Table:
        tid = f"{schema}.{name}"
        if tid not in self.tables:
            self.tables[tid] = Table(schema=schema, name=name)
        return self.tables[tid]


_INT = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint", "serial", "bigserial",
        "smallserial", "int2", "int4", "int8", "int1", "int16", "int32", "int64", "uint8",
        "uint16", "uint32", "uint64", "number"}
_DEC = {"decimal", "numeric", "money", "dec", "fixed"}
_FLT = {"float", "double", "real", "float4", "float8", "float32", "float64"}
_STR = {"char", "varchar", "text", "tinytext", "mediumtext", "longtext", "character", "nvarchar",
        "nchar", "string", "citext", "enum", "set", "uuid", "ntext", "fixedstring", "varchar2"}
_DATE = {"date", "datetime", "timestamp", "timestamptz", "time", "year", "datetime2", "date32"}
_BIN = {"blob", "tinyblob", "mediumblob", "longblob", "binary", "varbinary", "bytea"}
_BOOL = {"bool", "boolean", "bit"}


def type_family(type_str: str) -> str:
    """Coarse type family: int, decimal, float, string, datetime, json, binary, bool or unknown.

    Signedness and width are ignored on purpose: a signed key and an unsigned key still join.
    """
    t = (type_str or "").lower().strip()
    if not t:
        return "unknown"
    t = re.sub(r"^(nullable|lowcardinality)\(", "", t).rstrip(")")
    first = re.split(r"[\s(]", t, maxsplit=1)[0]
    if first == "double" or first == "character" and "varying" in t:
        return "float" if first == "double" else "string"
    if first in _INT or first.startswith("uint") or (first.startswith("int") and first[3:].isdigit()):
        return "int"
    if first in _DEC:
        return "decimal"
    if first in _FLT:
        return "float"
    if first in _STR:
        return "string"
    if first in _DATE or first.startswith("timestamp"):
        return "datetime"
    if first in {"json", "jsonb"}:
        return "json"
    if first in _BIN:
        return "binary"
    if first in _BOOL:
        return "bool"
    return "unknown"


def type_length(type_str: str) -> int | None:
    m = re.search(r"\((\d+)\)", type_str or "")
    return int(m.group(1)) if m else None
