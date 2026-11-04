"""Sibling-prefix matching: a column names its target without the prefix both tables share."""
from keygraph.graph import build_graph
from keygraph.model import Column, Index, Schema, Table


def _t(name, *cols):
    return Table("app", name, [Column("id", "int", False, 1, "PRI")] + [Column(c, "int", True, i + 2) for i, c in enumerate(cols)],
                 [Index("PRIMARY", ["id"], True, True)])


def _edges(*tables):
    s = Schema()
    for t in tables:
        s.add(t)
    return {(e["from"], tuple(e["from_columns"]), e["to"]): e for e in build_graph(s).data["edges"]}


def test_shared_prefix_finds_the_sibling_table():
    e = _edges(_t("ad_campaigns"), _t("ad_campaign_banners", "campaign_id"), _t("promo_banners"), _t("promo_banner_stats", "banner_id"))
    hit = e[("app.ad_campaign_banners", ("campaign_id",), "app.ad_campaigns")]
    assert any("prefix 'ad_'" in x or "prefix 'ad_campaign_'" in x for x in hit["evidence"])
    assert ("app.promo_banner_stats", ("banner_id",), "app.promo_banners") in e


def test_plain_names_still_win():
    e = _edges(_t("campaigns"), _t("ad_campaigns"), _t("ad_campaign_banners", "campaign_id"))
    assert ("app.ad_campaign_banners", ("campaign_id",), "app.ad_campaigns") in e or \
           ("app.ad_campaign_banners", ("campaign_id",), "app.campaigns") in e
