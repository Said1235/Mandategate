import pytest

from .conftest import WEI
from .helpers import TERMS, audit, model

U256_MAX = 2**256 - 1


def revert(w, msg):
    return w.vm.expect_revert(msg)


# ---------------------------------------------------------------- owner access
def test_owner_is_deployer_and_owner_only_methods(funded):
    a = funded.as_("alice")
    calls = [
        lambda: a.register_agent(funded.A["charlie"]),
        lambda: a.revoke_agent(funded.A["bob"]),
        lambda: a.create_mandate_version(1, 1, False, False, False, 1, 1, 1),
        lambda: a.revoke_mandate_version(1),
        lambda: a.register_vendor(TERMS),
        lambda: a.register_vendor_terms_version(1, TERMS + "x"),
        lambda: a.revoke_vendor_version(1, 1),
        lambda: a.revoke_authorization(1),
    ]
    for call in calls:
        with revert(funded, "only owner"):
            call()


def test_agent_cannot_change_own_status_or_policy(funded):
    a = funded.as_("alice")
    with revert(funded, "only owner"):
        a.revoke_agent(funded.A["alice"])
    with revert(funded, "only owner"):
        a.register_agent(funded.A["alice"])


# -------------------------------------------------------------- agent registry
def test_unregistered_agent_cannot_create_or_evaluate(funded):
    with revert(funded, "not an active agent"):
        funded.as_("charlie").create_purchase_request(1, 1, 1, WEI, 0)
    rid = funded.new_request()
    with revert(funded, "not an active agent"):
        funded.as_("charlie").evaluate_purchase_request(rid, TERMS)


def test_revoked_agent_cannot_create_or_evaluate_pending(funded):
    rid = funded.new_request()
    funded.as_("owner").revoke_agent(funded.A["alice"])
    with revert(funded, "not an active agent"):
        funded.as_("alice").create_purchase_request(1, 1, 1, WEI, 1)
    funded.mocks(model(), audit())
    with revert(funded, "not an active agent"):
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)
    assert funded.c.get_request(rid)["status"] == "PENDING_EVALUATION"


def test_agent_registry_lifecycle_and_zero_address(funded):
    o = funded.as_("owner")
    with revert(funded, "already active"):
        o.register_agent(funded.A["alice"])
    o.revoke_agent(funded.A["alice"])
    with revert(funded, "already revoked"):
        o.revoke_agent(funded.A["alice"])
    with revert(funded, "unknown agent"):
        o.revoke_agent(funded.A["charlie"])
    with revert(funded, "zero address"):
        o.register_agent(funded.Address(b"\x00" * 20))
    assert funded.c.get_agent(funded.A["alice"]) == {"registered": True, "active": False, "next_nonce": 0}
    o.register_agent(funded.A["alice"])  # reactivation keeps nonce
    assert funded.c.get_agent(funded.A["alice"])["active"] is True


def test_reactivation_keeps_nonce(funded):
    funded.new_request()
    o = funded.as_("owner")
    o.revoke_agent(funded.A["alice"])
    o.register_agent(funded.A["alice"])
    assert funded.c.get_agent(funded.A["alice"])["next_nonce"] == 1
    with revert(funded, "bad nonce"):
        funded.as_("alice").create_purchase_request(1, 1, 1, WEI, 0)


# --------------------------------------------------------------- nonce / replay
def test_nonce_replay_lower_skipped_and_overflow(funded):
    funded.new_request()
    a = funded.as_("alice")
    with revert(funded, "bad nonce"):
        a.create_purchase_request(1, 1, 1, WEI, 0)  # replay
    with revert(funded, "bad nonce"):
        a.create_purchase_request(1, 1, 1, WEI, 2)  # skipped
    a.create_purchase_request(1, 1, 1, WEI, 1)
    assert funded.c.get_agent(funded.A["alice"])["next_nonce"] == 2
    with revert(funded, "invalid integer"):
        a.create_purchase_request(1, 1, 1, WEI, -1)
    with revert(funded, "invalid integer"):
        a.create_purchase_request(1, 1, 1, WEI, U256_MAX + 1)


def test_failed_create_does_not_consume_nonce(funded):
    a = funded.as_("alice")
    with revert(funded, "per-purchase limit"):
        a.create_purchase_request(1, 1, 1, 3 * WEI, 0)
    assert funded.c.get_agent(funded.A["alice"])["next_nonce"] == 0
    assert funded.new_request() == 1


# ---------------------------------------------------- request identity / history
def test_request_ids_monotonic_and_per_request_records(funded):
    ids = [funded.new_request("alice"), funded.new_request("bob"), funded.new_request("alice")]
    assert ids == [1, 2, 3]
    r2 = funded.c.get_request(2)
    assert r2["agent"] == funded.A["bob"].as_hex and r2["nonce"] == 0
    assert funded.c.get_request(3)["nonce"] == 1
    with revert(funded, "unknown request"):
        funded.c.get_request(4)
    with revert(funded, "unknown request"):
        funded.c.get_request(0)


def test_request_binds_exact_versions_and_commitment(funded):
    funded.as_("owner").register_vendor_terms_version(1, TERMS + "7. Misc. Nothing else.\n")
    rid = funded.new_request(version=2)
    r = funded.c.get_request(rid)
    v2 = funded.c.get_vendor_version(1, 2)
    assert (r["vendor_id"], r["vendor_version"], r["mandate_version"]) == (1, 2, 1)
    assert r["terms_digest"] == v2["terms_digest"] != funded.c.get_vendor_version(1, 1)["terms_digest"]
    assert r["terms_byte_length"] == v2["terms_byte_length"]


def test_create_request_validations(funded):
    a = funded.as_("alice")
    with revert(funded, "unknown vendor version"):
        a.create_purchase_request(9, 1, 1, WEI, 0)
    with revert(funded, "unknown vendor version"):
        a.create_purchase_request(1, 9, 1, WEI, 0)
    with revert(funded, "unknown mandate version"):
        a.create_purchase_request(1, 1, 9, WEI, 0)
    with revert(funded, "must be positive"):
        a.create_purchase_request(1, 1, 1, 0, 0)
    with revert(funded, "invalid integer"):
        a.create_purchase_request(1, 1, 1, -5, 0)
    with revert(funded, "per-purchase limit"):
        a.create_purchase_request(1, 1, 1, 2 * WEI + 1, 0)
    a.create_purchase_request(1, 1, 1, 2 * WEI, 0)  # exactly at limit


def test_revoked_versions_block_create_and_evaluate(funded):
    rid = funded.new_request()
    funded.as_("owner").revoke_vendor_version(1, 1)
    with revert(funded, "vendor version revoked"):
        funded.as_("alice").create_purchase_request(1, 1, 1, WEI, 1)
    funded.mocks(model(), audit())
    with revert(funded, "vendor version revoked"):
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)

    funded.as_("owner").register_vendor_terms_version(1, TERMS)  # v2 same text, fresh version
    rid2 = funded.new_request(version=2)
    o = funded.as_("owner")
    o.revoke_mandate_version(1)
    with revert(funded, "mandate version revoked"):
        funded.as_("alice").evaluate_purchase_request(rid2, TERMS)
    with revert(funded, "mandate version revoked"):
        funded.as_("alice").create_purchase_request(1, 2, 1, WEI, 2)
    o = funded.as_("owner")
    with revert(funded, "already revoked"):
        o.revoke_mandate_version(1)
    with revert(funded, "already revoked"):
        o.revoke_vendor_version(1, 1)


# --------------------------------------------------------------- immutability
def test_mandate_versions_are_immutable_and_sequential(funded):
    o = funded.as_("owner")
    assert o.create_mandate_version(5, 9, True, True, True, 1, 2, 3) == 2
    m1 = funded.c.get_mandate_version(1)
    assert m1["max_per_purchase_wei"] == 2 * WEI and m1["allow_auto_renewal"] is False
    assert funded.c.get_mandate_version(2)["max_data_retention_days"] == 3
    with revert(funded, "unknown mandate version"):
        funded.c.get_mandate_version(3)


def test_mandate_input_validation(funded):
    o = funded.as_("owner")
    with revert(funded, "must be positive"):
        o.create_mandate_version(0, 1, False, False, False, 1, 1, 1)
    with revert(funded, "must be positive"):
        o.create_mandate_version(1, 0, False, False, False, 1, 1, 1)
    with revert(funded, "invalid integer"):
        o.create_mandate_version(1, 1, False, False, False, -1, 1, 1)
    with revert(funded, "invalid integer"):
        o.create_mandate_version(1, U256_MAX + 1, False, False, False, 1, 1, 1)
    o.create_mandate_version(U256_MAX, U256_MAX, False, False, False, U256_MAX, 0, 0)
    assert funded.c.get_mandate_version(2)["max_total_budget_wei"] == U256_MAX


def test_vendor_versions_are_immutable_and_independent(funded):
    o = funded.as_("owner")
    assert o.register_vendor("Other vendor terms. We never sell data.") == 2
    assert o.register_vendor_terms_version(1, TERMS + "x") == 2
    assert o.register_vendor_terms_version(1, TERMS + "xy") == 3
    assert funded.c.get_vendor_version(1, 1)["terms_digest"] == funded.c.compute_terms_commitment(TERMS)["terms_digest"]
    assert funded.c.get_vendor_version(2, 1)["vendor_id"] == 2
    with revert(funded, "unknown vendor"):
        o.register_vendor_terms_version(9, TERMS)
    with revert(funded, "unknown vendor"):
        o.register_vendor_terms_version(0, TERMS)
    with revert(funded, "unknown vendor version"):
        funded.c.get_vendor_version(1, 9)


def test_vendor_metadata_fields(funded):
    v = funded.c.get_vendor_version(1, 1)
    assert v["hash_algorithm"] == "keccak256" and v["commitment_schema_version"] == 1
    assert v["terms_byte_length"] == len(TERMS.encode()) and v["revoked"] is False
    assert not any("terms_text" in k for k in v)  # full terms are never stored
    assert "payout" not in str(v).lower()


def test_failed_vendor_registration_does_not_consume_ids(funded):
    o = funded.as_("owner")
    with revert(funded, "empty"):
        o.register_vendor("")
    assert o.register_vendor("Second vendor terms") == 2


# ---------------------------------------------------------------- cancel/revoke
def test_cancel_pending_access_and_state(funded):
    rid = funded.new_request("alice")
    with revert(funded, "only owner or the request's agent"):
        funded.as_("bob").cancel_pending_request(rid)
    funded.as_("alice").cancel_pending_request(rid)
    assert funded.c.get_request(rid)["status"] == "CANCELLED"
    with revert(funded, "only a pending request"):
        funded.as_("owner").cancel_pending_request(rid)
    funded.mocks(model(), audit())
    with revert(funded, "not pending"):
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)
    rid2 = funded.new_request("alice")
    funded.as_("owner").cancel_pending_request(rid2)
    assert funded.c.get_request(rid2)["status"] == "CANCELLED"


def test_state_machine_terminals(funded):
    ok = funded.evaluate(funded.new_request(), model(), audit(), TERMS)
    assert ok["status"] == "APPROVED"
    with revert(funded, "only a pending request"):
        funded.as_("alice").cancel_pending_request(ok["request_id"])
    with revert(funded, "not pending"):
        funded.as_("alice").evaluate_purchase_request(ok["request_id"], TERMS)
    funded.as_("owner").revoke_authorization(ok["request_id"])
    assert funded.c.get_request(ok["request_id"])["status"] == "REVOKED"
    with revert(funded, "only an APPROVED"):
        funded.as_("owner").revoke_authorization(ok["request_id"])
    with revert(funded, "not pending"):
        funded.as_("alice").evaluate_purchase_request(ok["request_id"], TERMS)

    den = funded.evaluate(
        funded.new_request(), model({"automatic_renewal": ("FOUND", True, "NONE")},
                                    {"automatic_renewal": ["The subscription does not renew automatically"]}),
        audit(), TERMS)
    assert den["status"] == "DENIED"
    with revert(funded, "only an APPROVED"):
        funded.as_("owner").revoke_authorization(den["request_id"])
    with revert(funded, "only a pending request"):
        funded.as_("owner").cancel_pending_request(den["request_id"])


def test_revoke_authorization_before_evaluation_rejected(funded):
    rid = funded.new_request()
    with revert(funded, "only an APPROVED"):
        funded.as_("owner").revoke_authorization(rid)


def test_no_payment_surface(funded):
    names = {n.lower() for n in dir(funded.c) if not n.startswith("_")}
    for banned in ("deposit", "withdraw", "transfer", "pay", "settle", "escrow", "refund", "execute"):
        assert not [n for n in names if banned in n], banned
