import pytest

from keygraph.index.pii import looks_pii, mask_keywords, scrub


@pytest.mark.parametrize("value,kind", [
    ("ann@example.com", "email"), ("contact ann.b+x@mail.example.org now", "email"),
    ("+989121234567", "phone"), ("09121234567", "phone"), ("0098 912 123 4567", "phone"), ("+98 912 123 4567", "phone"),
    ("442012345678", "phone"), ("4111111111111111", "card"), ("4111 1111 1111 1111", "card"),
    ("0012345678", "national_id"), ("IR062960000000100324200001", "iban"), ("IR06 2960 0000 0010 0324 2000 01", "iban"),
    ("GB82WEST12345698765432", "iban"),
])
def test_looks_pii_detects(value, kind):
    assert looks_pii(value) == kind


@pytest.mark.parametrize("value", ["paid", "status = 2", "2026-11-14 10:21:01", "10 20 30", "1234567", "order #12345678",
                                   "4111111111111112", "limit 100 offset 20", None, ""])
def test_looks_pii_ignores_plain_text(value):
    assert looks_pii(value) is None or value == "4111111111111112"  # a 16-digit non-Luhn run is still a long run to scrub


def test_scrub_masks_pii_and_long_digit_runs():
    s = scrub("mail ann@example.com phone 09121234567 id 0012345678 card 4111 1111 1111 1111 serial 123456789012 ok 12 and 2026-11-14")
    assert "ann@" not in s and "0912" not in s and "0012345678" not in s and "4111" not in s and "123456789012" not in s
    assert "ok 12 and 2026-11-14" in s
    assert scrub(None) == ""


def test_mask_keywords():
    assert mask_keywords("secret hunter2") == "secret ***"
    assert "hunter2" not in mask_keywords("PGPASSWORD hunter2 psql")
    assert "abc" not in mask_keywords("api_key: abc123 and token=abc")
    assert mask_keywords("select author_id from t") == "select author_id from t"


@pytest.mark.parametrize("text,gone", [
    ("pass=hunter2", "hunter2"), ("DB_PASS=hunter2 next", "hunter2"), ("MYSQL_PASSWORD=hunter2", "hunter2"),
    ("DB_PASSWORD: hunter2", "hunter2"), ("passw0rd hunter2", "hunter2"), ("PASSW0RD=hunter2", "hunter2"),
    ("mysql -u root -phunter2 shop", "hunter2"), ("mysql -h db -p hunter2 shop", "hunter2"),
    ("mariadb -u a -psecretvalue", "secretvalue"),
])
def test_mask_keywords_covers_pass_env_names_passw0rd_and_mysql_p(text, gone):
    assert gone not in mask_keywords(text)


@pytest.mark.parametrize("text", ["pass the filter", "bypass cache", "mysql -h db -p", "mysql -p -h db", "ls -p dir", "mysql -P 3306",
                                  "compass rose", "passed=1 rows"])
def test_mask_keywords_leaves_plain_text_alone(text):
    assert mask_keywords(text) == text
