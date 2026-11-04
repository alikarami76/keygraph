

def test_dicts_keyed_by_query_column_names_fall_back_to_order():
    from keygraph.adapters.base import dicts
    rows = [{"TABLE_SCHEMA": "app", "TABLE_NAME": "orders", "ORDINAL_POSITION": 1}]
    assert dicts(rows, ["schema", "table", "pos"]) == [{"schema": "app", "table": "orders", "pos": 1}]
    assert dicts([{"schema": "app", "table": "t"}], ["schema", "table"]) == [{"schema": "app", "table": "t"}]
