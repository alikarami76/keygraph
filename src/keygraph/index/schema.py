"""The answer schema for each indexing task kind, the instructions text, and the prompt hash.

The schema is a small JSON-Schema subset (type, properties, required, items, enum, minimum, maximum, maxLength,
maxItems, additionalProperties) checked by `check`, so no library is needed. Semantic checks (unknown tables,
PII-looking values, contradicting a declared FK) live in validate.py."""

from __future__ import annotations

import hashlib
import json
import math

SCHEMA_VERSION = 1
TASKS = ("relations", "columns", "tables", "concepts", "metrics", "performance")

REASON_MAX, TEXT_MAX, NAME_MAX, LIST_MAX, MAX_ITEMS = 240, 400, 120, 20, 50


def _s(n: int = TEXT_MAX, min_len: int = 1) -> dict:
    return {"type": "string", "minLength": min_len, "maxLength": n}


def _list(item: dict, n: int = LIST_MAX) -> dict:
    return {"type": "array", "items": item, "maxItems": n}


_CONF = {"type": "number", "minimum": 0, "maximum": 1}
_BASE = {"reason": _s(REASON_MAX), "confidence": _CONF}


def _item(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": {**props, **_BASE}, "required": [*required, "reason", "confidence"],
            "additionalProperties": False}


ITEMS = {
    "relations": _item({"from_table": _s(NAME_MAX), "from_columns": _list(_s(NAME_MAX), 6),
                        "to_table": _s(NAME_MAX), "to_columns": _list(_s(NAME_MAX), 6),
                        "cardinality": {"type": "string", "enum": ["many-to-one", "one-to-one", "one-to-many", "many-to-many"]}},
                       ["from_table", "from_columns", "to_table", "to_columns"]),
    "columns": _item({"table": _s(NAME_MAX), "column": _s(NAME_MAX), "meaning": _s(), "unit": _s(NAME_MAX),
                      "codes": {"type": "object", "additionalProperties": _s(NAME_MAX), "maxItems": 60},
                      "null_meaning": _s(REASON_MAX)}, ["table", "column", "meaning"]),
    "tables": _item({"table": _s(NAME_MAX), "purpose": _s(), "grain": _s(REASON_MAX), "grain_columns": _list(_s(NAME_MAX), 6),
                     "pitfalls": _list(_s(REASON_MAX), 10),
                     "default_filters": _list(_s(REASON_MAX), 10), "soft_delete": _s(REASON_MAX),
                     "freshness": _s(REASON_MAX)}, ["table", "purpose"]),
    "concepts": _item({"name": _s(NAME_MAX), "definition": _s(), "synonyms": _list(_s(NAME_MAX)),
                       "tables": _list(_s(NAME_MAX), 10)}, ["name", "definition", "tables"]),
    "metrics": _item({"name": _s(NAME_MAX), "definition": _s(), "expression": _s(), "grain": _s(REASON_MAX),
                      "tables": _list(_s(NAME_MAX), 10), "filters": _s(REASON_MAX), "caveats": _s(REASON_MAX)},
                     ["name", "definition", "expression", "tables"]),
    "performance": _item({"table": _s(NAME_MAX), "common_filters": _list(_s(REASON_MAX), 10),
                          "slow_patterns": _list(_s(REASON_MAX), 10), "index_friendly_filters": _list(_s(REASON_MAX), 10),
                          "hints": _list(_s(REASON_MAX), 10)}, ["table"]),
}

INSTRUCTIONS = {
    "relations": "relations: list foreign-key style links the schema does not declare, from a table of this unit to a unit "
                 "table or to a neighbour listed in the packet. Write tables as schema.table. Give from/to table and columns, the reason (what in the packet supports it), "
                 "and your confidence. Do not repeat declared relations.",
    "columns": "columns: give the business meaning of columns whose name alone does not say it: unit, code-to-meaning map for "
               "enum or status columns, and what NULL means. Skip obvious columns.",
    "tables": "tables: for each table give its purpose, its grain (what one row is, and optionally `grain_columns`, the columns that identify a row), pitfalls, default filters, how "
              "soft delete works and how fresh the data is, when the packet shows it.",
    "concepts": "concepts: name the business terms this unit holds, with a definition, synonyms in any language, and the "
                "tables that hold them.",
    "metrics": "metrics: only metrics the packet supports: definition, a SQL expression, grain, tables, filters, caveats.",
    "performance": "performance: per table, the filters queries commonly use, slow patterns, filters that fit the existing "
                   "indexes, and partition or sort hints.",
}

EXAMPLES = {
    "relations": {"from_table": "shop.shipments", "from_columns": ["order_id"], "to_table": "shop.orders", "to_columns": ["id"],
                  "cardinality": "many-to-one", "reason": "order_id values and queries join on it", "confidence": 0.7},
    "columns": {"table": "shop.orders", "column": "status", "meaning": "Order lifecycle state", "codes": {"1": "new", "2": "paid"},
                "null_meaning": "never set", "reason": "context note lists the codes", "confidence": 0.8},
    "tables": {"table": "shop.orders", "purpose": "One checkout by a customer", "grain": "one row per order",
               "default_filters": ["deleted_at IS NULL"], "pitfalls": ["total includes shipping"],
               "reason": "comment and queries agree", "confidence": 0.8},
    "concepts": {"name": "order", "definition": "A checkout by a customer", "synonyms": ["purchase", "checkout"],
                 "tables": ["shop.orders", "shop.order_items"], "reason": "table comments", "confidence": 0.8},
    "metrics": {"name": "paid revenue", "definition": "Sum of paid order totals", "expression": "SUM(o.total)",
                "tables": ["shop.orders"], "filters": "status = 2", "reason": "seen in logged queries", "confidence": 0.6},
    "performance": {"table": "shop.orders", "common_filters": ["status", "placed_at"], "slow_patterns": ["status without date range"],
                    "index_friendly_filters": ["customer_id"], "hints": ["filter placed_at first"],
                    "reason": "index and query log", "confidence": 0.6},
}


def kinds_of(tasks) -> list[str]:
    """Task kinds in canonical order; raises ValueError on an unknown kind."""
    tasks = list(tasks)
    bad = [t for t in tasks if t not in TASKS]
    if bad:
        raise ValueError(f"unknown task kind: {', '.join(bad)}")
    return [t for t in TASKS if t in tasks]


def answer_schema(tasks=TASKS) -> dict:
    """JSON Schema of an answer for the given task kinds: {"model"?: str, "<kind>": [items]} with a list per kind."""
    kinds = kinds_of(tasks)
    return {"type": "object", "properties": {"model": _s(NAME_MAX), **{k: _list(ITEMS[k], MAX_ITEMS) for k in kinds}},
            "required": kinds, "additionalProperties": False, "schema_version": SCHEMA_VERSION}


def instructions(tasks=TASKS) -> str:
    """The task text an agent reads with a packet."""
    kinds = kinds_of(tasks)
    return ("Answer with one JSON object holding a list for each of: " + ", ".join(kinds) + ". Use only the tables and "
            "columns the packet names (unit tables, their columns, and listed neighbours). Every item needs a short `reason` and a `confidence` from 0 to 1. Never write personal "
            "values (emails, phone numbers, card or national ids). Leave a list empty when the packet gives no support. "
            "Optional top-level `model` names the model you are.\n" + "\n".join(INSTRUCTIONS[k] for k in kinds)
            + "\nOne valid item per kind (the examples are part of the prompt hash):\n"
            + "\n".join(f"{k}: {canonical(EXAMPLES[k])}" for k in kinds))


def examples(tasks=TASKS) -> dict:
    return {k: [EXAMPLES[k]] for k in kinds_of(tasks)}


def canonical(obj) -> str:
    """Canonical JSON: sorted keys, no spaces, non-ASCII kept. Byte-stable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prompt_hash(instructions_text: str, schema: dict, packet: dict) -> str:
    """sha256 over instructions + schema + canonical packet JSON."""
    return hashlib.sha256("\n".join((instructions_text, canonical(schema), canonical(packet))).encode("utf-8")).hexdigest()


def _type_ok(v, t: str) -> bool:
    return {"object": isinstance(v, dict), "array": isinstance(v, list), "string": isinstance(v, str),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool), "integer": isinstance(v, int) and not isinstance(v, bool),
            "boolean": isinstance(v, bool)}[t]


def check(value, schema: dict, path: str = "$") -> list[str]:
    """Errors (as 'path: message') of a value against a schema; an empty list means it fits."""
    t = schema.get("type")
    if t and not _type_ok(value, t):
        return [f"{path}: expected {t}"]
    errs: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: must be one of {', '.join(map(str, schema['enum']))}")
    if t == "number":
        if isinstance(value, float) and not math.isfinite(value):
            return [f"{path}: must be a finite number"]
        if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: must be between {schema.get('minimum')} and {schema.get('maximum')}")
    elif t == "string":
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{path}: must not be empty")
        if len(value) > schema.get("maxLength", 10**9):
            errs.append(f"{path}: longer than {schema['maxLength']} characters")
    elif t == "array":
        if len(value) > schema.get("maxItems", 10**9):
            errs.append(f"{path}: more than {schema['maxItems']} items")
        for i, v in enumerate(value if "items" in schema else []):
            errs += check(v, schema["items"], f"{path}[{i}]")
    elif t == "object":
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing {k}")
        extra = schema.get("additionalProperties", True)
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} entries")
        for k in sorted(value):
            if k in props:
                errs += check(value[k], props[k], f"{path}.{k}")
            elif extra is False:
                errs.append(f"{path}: unknown field {k}")
            elif isinstance(extra, dict):
                errs += check(value[k], extra, f"{path}.{k}")
    return errs


def validate_answer(answer, tasks=TASKS) -> list[str]:
    """Structure errors of an answer for the given task kinds (shape, types, ranges, lengths). Not semantic."""
    return check(answer, answer_schema(tasks))
