"""Generic naming rules for FK-less databases: shared key name, reference prefixes, numbered roles,
<table>_<column> and bare id. Each rule has a positive test and a near-miss negative test."""
from keygraph.graph import build_graph
from keygraph.model import Column, Index, Schema, Table


def T(name, cols, pk=(), uniq=(), schema="s"):
    """cols: list of (name, type)."""
    ix = ([Index("PRIMARY", list(pk), True, True)] if pk else []) + [
        Index(f"uq_{u}", [u], True, False) for u in uniq]
    return Table(schema, name, [Column(n, ty, True, i + 1) for i, (n, ty) in enumerate(cols)], ix)


def graph(*tables):
    s = Schema()
    for t in tables:
        s.add(t)
    return build_graph(s)


def edge(g, frm, col, to=None):
    return next((e for e in g.edges if e["from"] == frm and e["from_columns"] == [col]
                 and (to is None or e["to"] == to)), None)


def ev(e, text):
    return any(text in x for x in e["evidence"])


# (a) shared key name
def test_a_shared_key_name_links_unrelated_names():
    g = graph(T("schools", [("school_code", "varchar(14)"), ("title", "text")], pk=["school_code"]),
              T("frpm", [("school_code", "varchar(14)"), ("n", "int")]))
    e = edge(g, "s.frpm", "school_code", "s.schools")
    assert e and e["to_columns"] == ["school_code"] and e["confidence"] == 0.75  # 0.70+0.10+0.03+0.08+0.05, capped
    assert ev(e, "capped at 0.75")
    assert ev(e, "column name 'school_code' equals the key column of s.schools (rule: shared key name)")


def test_a_unique_generic_key_is_not_a_reference():
    g = graph(T("users", [("id", "int"), ("uuid", "varchar(36)"), ("token", "varchar(40)")], pk=["id"], uniq=["uuid", "token"]),
              T("audit_log", [("uuid", "varchar(36)")]), T("events", [("uuid", "varchar(36)")]),
              T("sessions", [("token", "varchar(40)")]))
    assert not [e for e in g.edges if e["confidence"] >= 0.6]


def test_a_non_generic_unique_key_target_is_kept_and_capped():
    g = graph(T("cards", [("id", "int"), ("card_ref", "varchar(36)")], pk=["id"], uniq=["card_ref"]),
              T("legalities", [("id", "int"), ("card_ref", "varchar(36)")], pk=["id"]))
    e = edge(g, "s.legalities", "card_ref", "s.cards")
    assert e and e["to_columns"] == ["card_ref"] and e["cardinality"] == "many-to-one" and e["confidence"] == 0.75


def test_a_pk_target_of_a_pk_only_name_is_kept():
    g = graph(T("cards", [("uuid", "varchar(36)")], pk=["uuid"]),
              T("legalities", [("id", "int"), ("uuid", "varchar(36)")], pk=["id"]))
    assert edge(g, "s.legalities", "uuid", "s.cards")["confidence"] == 0.75


def test_a_two_tables_with_the_key_name_gives_no_edge():
    g = graph(T("schools", [("school_code", "varchar(14)")], pk=["school_code"]),
              T("campuses", [("school_code", "varchar(14)")], pk=["school_code"]),
              T("frpm", [("school_code", "varchar(14)"), ("n", "int")]))
    assert edge(g, "s.frpm", "school_code") is None


def test_a_generic_id_and_short_names_are_not_shared_keys():
    g = graph(T("a", [("id", "int")], pk=["id"]), T("b", [("id", "int"), ("x", "int")], pk=["id"]),
              T("c", [("no", "int")], pk=["no"]), T("d", [("no", "int")]))
    assert edge(g, "s.b", "id", "s.a") is None
    assert edge(g, "s.d", "no") is None


# (b) reference prefixes
def test_b_reference_prefix_is_stripped():
    g = graph(T("members", [("member_id", "varchar(10)")], pk=["member_id"]),
              T("attendance", [("link_to_member", "varchar(10)"), ("n", "int")]))
    e = edge(g, "s.attendance", "link_to_member", "s.members")
    assert e and ev(e, "reference prefix 'link_to_' stripped") and e["confidence"] == 0.94
    # 0.68 + types 0.10 + 0.03 same length + pk 0.08 + same schema 0.05


def test_b_ref_fk_id_prefixes():
    g = graph(T("users", [("id", "int")], pk=["id"]),
              T("a", [("ref_user", "int")]), T("b", [("fk_user", "int")]), T("c", [("id_user", "int")]))
    for n, col in (("a", "ref_user"), ("b", "fk_user"), ("c", "id_user")):
        assert edge(g, f"s.{n}", col, "s.users"), col


def test_b_near_miss_not_the_exact_prefix():
    g = graph(T("members", [("member_id", "int")], pk=["member_id"]),
              T("x", [("reference", "int"), ("prefix_member", "int"), ("refmember", "int"),
                      ("linkmember", "int"), ("identity", "int")]))
    for col in ("reference", "prefix_member", "refmember", "linkmember", "identity"):
        assert edge(g, "s.x", col, "s.members") is None, col


# (c) numbered role columns
def _players():
    return T("players", [("id", "int"), ("player_ref_id", "int"), ("name", "text")], pk=["id"], uniq=["player_ref_id"])


def test_c_numbered_role_columns_target_the_named_unique_key():
    g = graph(_players(), T("matches", [("id", "int"), ("home_player_1", "int"), ("away_player_2", "int"),
                                         ("home_player_3", "int")], pk=["id"]))
    for col in ("home_player_1", "away_player_2", "home_player_3"):
        e = edge(g, "s.matches", col, "s.players")
        assert e and e["to_columns"] == ["player_ref_id"], col
        assert ev(e, "numbered role column")


def test_c_numbered_role_columns_fall_back_to_the_primary_key():
    g = graph(T("players", [("id", "int")], pk=["id"]), T("matches", [("player_1", "int"), ("player_2", "int")]))
    assert edge(g, "s.matches", "player_1", "s.players")["to_columns"] == ["id"]


def test_c_numbered_columns_that_are_not_references():
    g = graph(_players(), T("addr", [("address_line_1", "text"), ("line2", "text"), ("sha256", "text"),
                                      ("rank_1", "int"), ("home_1", "int")]))
    assert not [e for e in g.edges if e["from"] == "s.addr"]


# (d) table_column
def test_d_table_column_and_camel_case():
    g = graph(T("sets", [("code", "varchar(8)"), ("name", "text")], pk=["code"]),
              T("set_translations", [("id", "int"), ("setCode", "varchar(8)")], pk=["id"]))
    e = edge(g, "s.set_translations", "setCode", "s.sets")
    assert e and e["to_columns"] == ["code"] and ev(e, "rule: table_column") and e["confidence"] == 0.75
    g2 = graph(T("sets", [("code", "varchar(8)")], pk=["code"]), T("t", [("set_code", "varchar(8)")]))
    assert edge(g2, "s.t", "set_code", "s.sets")


def test_d_near_miss_column_is_not_a_key_of_that_table():
    g = graph(T("sets", [("id", "int"), ("code", "varchar(8)"), ("name", "text")], pk=["id"]),
              T("t", [("set_code", "varchar(8)"), ("set_name", "text"), ("sets_total", "int")]))
    assert not [e for e in g.edges if e["from"] == "s.t"]


def test_d_normal_id_columns_unchanged():
    g = graph(T("customers", [("id", "int")], pk=["id"]), T("orders", [("customer_id", "int")]))
    e = edge(g, "s.orders", "customer_id", "s.customers")
    assert e and not ev(e, "table_column")


# (e) bare id
def test_e_bare_id_without_own_key_links_to_the_only_id_table():
    g = graph(T("patients", [("ID", "int"), ("sex", "text")], pk=["ID"]),
              T("examinations", [("ID", "int"), ("score", "int")]),
              T("codes", [("code", "text")], pk=["code"]))
    e = edge(g, "s.examinations", "ID", "s.patients")
    assert e and ev(e, "rule: bare id") and e["confidence"] == 0.75
    assert edge(g, "s.patients", "ID") is None


def test_e_name_token_picks_between_several_id_tables():
    g = graph(T("patients", [("id", "int")], pk=["id"]), T("doctors", [("id", "int")], pk=["id"]),
              T("doctor_notes", [("id", "int"), ("n", "text")]))
    assert edge(g, "s.doctor_notes", "id", "s.doctors") and not edge(g, "s.doctor_notes", "id", "s.patients")


def test_e_near_misses():
    # own primary key: no edge; several id tables and no name match: no edge
    g = graph(T("patients", [("id", "int")], pk=["id"]),
              T("exams", [("id", "int"), ("n", "int")], pk=["id"]))
    assert edge(g, "s.exams", "id") is None
    g = graph(T("patients", [("id", "int")], pk=["id"]), T("doctors", [("id", "int")], pk=["id"]),
              T("exams", [("id", "int")]))
    assert edge(g, "s.exams", "id") is None


def test_e_a_copy_of_a_table_in_another_schema_is_not_linked_by_the_fallback():
    g = graph(T("loads", [("id", "int")], pk=["id"], schema="rpt"),
              T("orders", [("id", "int"), ("n", "int")], schema="rpt"),
              T("orders", [("id", "int"), ("n", "int")], pk=["id"], schema="shop"))
    assert edge(g, "rpt.orders", "id") is None


# precision guards
def test_a_generic_attribute_names_are_not_shared_keys():
    g = graph(T("vouchers", [("code", "text"), ("email", "text")], pk=["code"], uniq=["email"]),
              T("customers", [("email", "text"), ("code", "text")]))
    assert not [e for e in g.edges if e["from"] == "s.customers"]


def test_a_key_to_key_match_is_capped_below_the_display_floor():
    g = graph(T("cards", [("uuid", "varchar(36)")], pk=["uuid"]), T("legalities", [("uuid", "varchar(36)")], pk=["uuid"]))
    e = edge(g, "s.legalities", "uuid", "s.cards")
    assert e and e["confidence"] <= 0.55 and ev(e, "a shared name alone is weak")


def test_a_does_not_link_a_table_to_its_copy_in_a_related_schema():
    g = graph(T("cards", [("uuid", "varchar(36)")], pk=["uuid"], schema="app"),
              T("cards_copy", [("uuid", "varchar(36)"), ("n", "int")], schema="ods_app"))
    assert edge(g, "ods_app.cards_copy", "uuid") is None


def test_c_and_e_are_capped_without_exact_type_and_index():
    g = graph(T("players", [("id", "int")], pk=["id"]), T("matches", [("player_1", "int")]))
    e = edge(g, "s.matches", "player_1", "s.players")
    assert e and e["confidence"] <= 0.75 and ev(e, "capped at 0.75")
    g = graph(T("patients", [("ID", "int")], pk=["ID"]), T("exams", [("ID", "int")]))
    assert edge(g, "s.exams", "ID")["confidence"] <= 0.75


def test_d_copied_attributes_are_not_references():
    g = graph(T("users", [("id", "int"), ("email", "text"), ("name", "text"), ("token", "text")], pk=["id"],
                uniq=["email", "token"]),
              T("order", [("id", "int"), ("number", "text")], pk=["id"], uniq=["number"]),
              T("orders", [("user_email", "text"), ("user_name", "text"), ("user_token", "text"), ("order_number", "text")]))
    assert not [e for e in g.edges if e["from"] == "s.orders"]


def test_e_keyless_table_named_after_another_is_capped_at_070():
    g = graph(T("users", [("id", "int")], pk=["id"]), T("user_notes", [("id", "int"), ("n", "text")]))
    assert edge(g, "s.user_notes", "id", "s.users")["confidence"] == 0.70


def test_normal_stem_columns_are_untouched_by_the_new_rules():
    from keygraph.infer import Inferer, Options
    s = Schema()
    for t in (T("customers", [("id", "int"), ("customer_ref", "int"), ("email", "text")], pk=["id"], uniq=["customer_ref"]),
              T("accounts", [("id", "int"), ("customer_ref", "int")], pk=["id"], uniq=["customer_ref"]),
              T("orders", [("id", "int"), ("customer_id", "int"), ("customerRef", "int"), ("buyer_user_id", "int"),
                           ("parent_id", "int")], pk=["id"]),
              T("users", [("id", "int")], pk=["id"])):
        s.add(t)
    old = Inferer(s, Options()); old.extra_rules = False
    new = Inferer(s, Options())
    keep = lambda es: [e for e in es if e["from_columns"][0] in ("customer_id", "buyer_user_id", "parent_id")]
    assert keep(new.run()) == keep(old.run()) and keep(old.run())


def test_many_keyless_tables_stay_fast():
    import time
    from keygraph.infer import Inferer, Options
    s = Schema()
    for i in range(1500):
        s.add(T(f"tbl{i}", [(f"c{j}", "int") for j in range(20)] + [("id", "int")], schema="s"))
    s.add(T("hub", [("id", "int")], pk=["id"]))
    inf = Inferer(s, Options())
    t0 = time.perf_counter()
    inf.run()
    assert time.perf_counter() - t0 < 5  # was about 20x slower before the per-schema maps
