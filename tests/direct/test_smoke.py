from .conftest import WEI
from .helpers import TERMS, audit, model


def test_happy_path(funded):
    rid = funded.new_request()
    rec = funded.evaluate(rid, model(), audit(), TERMS)
    assert rec["status"] == "APPROVED", rec
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == WEI
