import itertools

import pytest

from .conftest import WEI
from .helpers import FIELDS, NOT_FOUND, Q, TERMS, ambiguous, audit, fence, model

U256_MAX = 2**256 - 1
STATUSES = ("FOUND", "NOT_FOUND", "AMBIGUOUS")
MANDATE = {
    "allow_auto_renewal": False,
    "allow_customer_data_training": False,
    "allow_third_party_sharing": False,
    "max_minimum_commitment_days": 365,
    "max_cancellation_notice_days": 30,
    "max_data_retention_days": 30,
}
COMPLIANT = (False, False, False, 365, 30, 30)
VIOLATING = (True, True, True, 366, 31, 31)


def finding(i, status, value):
    bv = value if (i < 3 and status == "FOUND") else None
    dv = value if (i >= 3 and status == "FOUND") else None
    return {
        "field": FIELDS[i], "status": status, "bool_value": bv, "days_value": dv,
        "ambiguity_code": "UNCLEAR_SCOPE" if status == "AMBIGUOUS" else "NONE", "evidence": [],
    }


# ------------------------------------------------- deterministic decision engine
def test_all_729_status_combinations_without_violations(funded):
    n = 0
    for combo in itertools.product(STATUSES, repeat=6):
        fs = [finding(i, s, COMPLIANT[i]) for i, s in enumerate(combo)]
        decision, reasons = funded.mod._derive(fs, MANDATE)
        n += 1
        if all(s == "FOUND" for s in combo):
            assert (decision, reasons) == ("APPROVED", [])
        else:
            assert decision == "REVIEW"  # never APPROVED, never DENIED without a violation
            assert reasons == [f"INCOMPLETE:{FIELDS[i]}:{s}" for i, s in enumerate(combo) if s != "FOUND"]
    assert n == 729


@pytest.mark.parametrize("field_idx", range(6))
def test_each_single_violation_denies_with_any_other_statuses(funded, field_idx):
    """A confirmed violation always wins over missing/ambiguous information (3^5 combos each)."""
    for others in itertools.product(STATUSES, repeat=5):
        combo = list(others)
        combo.insert(field_idx, "FOUND")
        fs = [finding(i, s, VIOLATING[i] if i == field_idx else COMPLIANT[i]) for i, s in enumerate(combo)]
        decision, reasons = funded.mod._derive(fs, MANDATE)
        assert decision == "DENIED"
        assert reasons[0] == "VIOLATION:" + FIELDS[field_idx]


def test_multiple_simultaneous_violations_all_reported(funded):
    fs = [finding(i, "FOUND", VIOLATING[i]) for i in range(6)]
    decision, reasons = funded.mod._derive(fs, MANDATE)
    assert decision == "DENIED" and reasons == ["VIOLATION:" + f for f in FIELDS]


@pytest.mark.parametrize("idx,limit_key", [(3, "max_minimum_commitment_days"), (4, "max_cancellation_notice_days"), (5, "max_data_retention_days")])
def test_duration_boundaries_have_no_tolerance(funded, idx, limit_key):
    lim = MANDATE[limit_key]
    for val, expected in ((lim - 1, "APPROVED"), (lim, "APPROVED"), (lim + 1, "DENIED")):
        vals = list(COMPLIANT)
        vals[idx] = val
        fs = [finding(i, "FOUND", vals[i]) for i in range(6)]
        assert funded.mod._derive(fs, MANDATE)[0] == expected


def test_zero_day_limits_and_values(funded):
    m = dict(MANDATE, max_minimum_commitment_days=0, max_cancellation_notice_days=0, max_data_retention_days=0)
    ok = [finding(i, "FOUND", (False, False, False, 0, 0, 0)[i]) for i in range(6)]
    assert funded.mod._derive(ok, m)[0] == "APPROVED"
    bad = [finding(i, "FOUND", (False, False, False, 0, 1, 0)[i]) for i in range(6)]
    assert funded.mod._derive(bad, m)[0] == "DENIED"


def test_true_is_compliant_when_mandate_allows_it(funded):
    m = dict(MANDATE, allow_auto_renewal=True, allow_customer_data_training=True, allow_third_party_sharing=True)
    fs = [finding(i, "FOUND", VIOLATING[i] if i < 3 else COMPLIANT[i]) for i in range(6)]
    assert funded.mod._derive(fs, m)[0] == "APPROVED"


def test_missing_is_never_zero_or_false(funded):
    for i in range(6):
        combo = ["FOUND"] * 6
        combo[i] = "NOT_FOUND"
        fs = [finding(j, s, COMPLIANT[j]) for j, s in enumerate(combo)]
        assert funded.mod._derive(fs, MANDATE)[0] == "REVIEW"


def test_decision_view_covers_every_decision_bearing_component(funded):
    fs = [finding(i, "FOUND", COMPLIANT[i]) for i in range(6)]
    base = funded.mod._decision_view(fs)
    for i in range(6):
        for key, new in (("status", "NOT_FOUND"), ("bool_value", True), ("days_value", 7), ("ambiguity_code", "OTHER_AMBIGUITY")):
            t = [dict(f) for f in fs]
            t[i][key] = new
            assert funded.mod._decision_view(t) != base, (FIELDS[i], key)


# ----------------------------------------------------- decisions through contract
@pytest.mark.parametrize("field,bad", [
    ("automatic_renewal", True), ("customer_data_training", True), ("third_party_sharing", True),
    ("minimum_commitment_days", 366), ("cancellation_notice_days", 31), ("data_retention_days", 31)])
def test_contract_denies_each_violation_and_stores_reasons(funded, field, bad):
    r = funded.evaluate(funded.new_request(), model({field: ("FOUND", bad, "NONE")}), audit(), TERMS)
    assert r["status"] == r["decision"] == "DENIED"
    assert f"VIOLATION:{field}" in r["reasons_json"]
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == 0


def test_contract_review_on_not_found_and_ambiguous_and_denied_beats_review(funded):
    r = funded.evaluate(funded.new_request(), model({"third_party_sharing": NOT_FOUND}), audit(), TERMS)
    assert r["status"] == "REVIEW" and "INCOMPLETE:third_party_sharing:NOT_FOUND" in r["reasons_json"]
    r = funded.evaluate(funded.new_request(), model({"data_retention_days": ambiguous()}), audit(), TERMS)
    assert r["status"] == "REVIEW"
    spec = {"customer_data_training": ("FOUND", True, "NONE"), "data_retention_days": NOT_FOUND}
    r = funded.evaluate(funded.new_request(), model(spec), audit(), TERMS)
    assert r["status"] == "DENIED" and "INCOMPLETE:data_retention_days:NOT_FOUND" in r["reasons_json"]
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == 0


def test_all_not_found_and_all_ambiguous_are_review(funded):
    spec_nf = {f: NOT_FOUND for f in FIELDS}
    spec_am = {f: ambiguous("OTHER_AMBIGUITY") for f in FIELDS}
    for spec in (spec_nf, spec_am):
        r = funded.evaluate(funded.new_request(), model(spec), audit(), TERMS)
        assert r["status"] == "REVIEW"


def test_approved_record_stores_complete_agreed_findings(funded):
    import json
    r = funded.evaluate(funded.new_request(), model(), audit(), TERMS)
    env = json.loads(r["findings_json"])
    assert set(env) == {"schema_version", "request_id", "vendor_id", "vendor_version", "mandate_version",
                        "terms_digest", "terms_byte_length", "findings"}
    assert env["request_id"] == r["request_id"] and env["terms_digest"] == r["terms_digest"]
    assert [f["field"] for f in env["findings"]] == list(FIELDS)
    assert all(f["evidence"] for f in env["findings"])
    assert "summary" not in r["findings_json"].lower() and "score" not in r["findings_json"].lower()


# ----------------------------------------------------------------------- budget
def test_accounting_exactly_at_cap_and_one_above(funded):
    r1 = funded.evaluate(funded.new_request(amount=2 * WEI), model(), audit(), TERMS)
    r2 = funded.evaluate(funded.new_request(amount=WEI), model(), audit(), TERMS)
    assert (r1["status"], r2["status"]) == ("APPROVED", "APPROVED")
    assert funded.c.get_budget_state(1) == {"mandate_version": 1, "max_total_budget_wei": 3 * WEI,
                                            "authorized_total_wei": 3 * WEI, "remaining_wei": 0}
    rid = funded.new_request(amount=1)  # one wei above the cap
    funded.mocks(model(), audit())
    with funded.vm.expect_revert("cap exceeded"):
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)
    assert funded.c.get_request(rid)["status"] == "PENDING_EVALUATION"
    assert r2["authorized_total_after"] == 3 * WEI


def test_cumulative_total_checked_for_individually_compliant_requests(funded):
    a = funded.evaluate(funded.new_request(amount=2 * WEI), model(), audit(), TERMS)
    rid = funded.new_request(amount=2 * WEI)  # compliant alone, 4 > 3 cumulatively
    funded.mocks(model(), audit())
    with funded.vm.expect_revert("cap exceeded"):
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)
    assert a["authorized_total_after"] == 2 * WEI
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == 2 * WEI


def test_multiple_pending_requests_all_counted_only_on_approval(funded):
    agents = ["alice", "bob", "alice", "bob"]
    ids = [funded.new_request(who) for who in agents]
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == 0  # pending never counts
    statuses = []
    for rid, who in reversed(list(zip(ids, agents))):  # evaluate out of creation order
        funded.mocks(model(), audit())
        try:
            funded.as_(who).evaluate_purchase_request(rid, TERMS)
            statuses.append(funded.c.get_request(rid)["status"])
        except Exception as e:  # the 4th approval would exceed the 3 GEN cap
            assert "cap exceeded" in str(e)
            statuses.append("CAP")
    assert statuses == ["APPROVED", "APPROVED", "APPROVED", "CAP"]
    assert funded.c.get_request(ids[0])["status"] == "PENDING_EVALUATION"  # the one that hit the cap
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == 3 * WEI


def test_denied_review_and_failed_consensus_never_count(funded):
    funded.evaluate(funded.new_request(), model({"automatic_renewal": ("FOUND", True, "NONE")}), audit(), TERMS)
    funded.evaluate(funded.new_request(), model({"data_retention_days": NOT_FOUND}), audit(), TERMS)
    rid = funded.new_request()
    funded.vm.clear_mocks()
    with pytest.raises(Exception):
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)  # leader LLM unavailable
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == 0
    assert funded.c.get_request(rid)["status"] == "PENDING_EVALUATION"


def test_duplicate_evaluation_cannot_double_count(funded):
    r = funded.evaluate(funded.new_request(), model(), audit(), TERMS)
    for _ in range(2):
        with funded.vm.expect_revert("not pending"):
            funded.as_("alice").evaluate_purchase_request(r["request_id"], TERMS)
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == WEI


def test_revocation_and_cancellation_do_not_erase_authorized_history(funded):
    r = funded.evaluate(funded.new_request(), model(), audit(), TERMS)
    funded.as_("owner").revoke_authorization(r["request_id"])
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == WEI
    rid = funded.new_request()
    funded.as_("alice").cancel_pending_request(rid)
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == WEI


def test_new_mandate_versions_have_independent_caps(funded):
    funded.evaluate(funded.new_request(amount=2 * WEI), model(), audit(), TERMS)
    funded.as_("owner").create_mandate_version(WEI, WEI, False, False, False, 365, 30, 30)
    assert funded.c.get_budget_state(2)["authorized_total_wei"] == 0  # not inherited
    r = funded.evaluate(funded.new_request(mandate=2, amount=WEI), model(), audit(), TERMS)
    assert r["status"] == "APPROVED" and r["mandate_version"] == 2
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == 2 * WEI
    assert funded.c.get_budget_state(2) == {"mandate_version": 2, "max_total_budget_wei": WEI,
                                            "authorized_total_wei": WEI, "remaining_wei": 0}


def test_request_stays_bound_to_its_mandate_version(funded):
    rid = funded.new_request()  # bound to mandate 1 (strict)
    funded.as_("owner").create_mandate_version(WEI, 10 * WEI, True, True, True, 9999, 9999, 9999)  # permissive v2
    spec = {"automatic_renewal": ("FOUND", True, "NONE")}
    r = funded.evaluate(rid, model(spec), audit(), TERMS)
    assert r["mandate_version"] == 1 and r["status"] == "DENIED"  # judged by v1, not by the new version


def test_u256_boundaries_do_not_wrap(funded):
    funded.as_("owner").create_mandate_version(U256_MAX, U256_MAX, False, False, False, 365, 30, 30)
    r = funded.evaluate(funded.new_request(mandate=2, amount=U256_MAX), model(), audit(), TERMS)
    assert r["status"] == "APPROVED" and r["authorized_total_after"] == U256_MAX
    rid = funded.new_request(mandate=2, amount=1)
    funded.mocks(model(), audit())
    with funded.vm.expect_revert("cap exceeded"):
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)
    assert funded.c.get_budget_state(2)["remaining_wei"] == 0


# -------------------------------------------------------------- error atomicity
def test_leader_llm_failure_leaves_request_pending_and_retryable(funded):
    rid = funded.new_request()
    funded.vm.clear_mocks()
    with pytest.raises(Exception):
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)
    rec = funded.c.get_request(rid)
    assert rec["status"] == "PENDING_EVALUATION" and rec["findings_json"] == "" and rec["decision"] == ""
    assert funded.evaluate(rid, model(), audit(), TERMS)["status"] == "APPROVED"  # retry works


@pytest.mark.parametrize("bad", [
    "APPROVED", fence({"decision": "APPROVED"}), fence([1, []]), model(quotes={"automatic_renewal": ["not in the doc"]}),
    model(quotes={"automatic_renewal": [Q["automatic_renewal"], Q["automatic_renewal"]]}),
    model({"minimum_commitment_days": ("FOUND", "365", "NONE")}),
])
def test_malformed_or_unsupported_model_output_never_changes_state(funded, bad):
    rid = funded.new_request()
    funded.mocks(bad, audit())
    with pytest.raises(Exception) as ei:
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)
    assert "[LLM_ERROR]" in str(ei.value)
    rec = funded.c.get_request(rid)
    assert (rec["status"], rec["findings_json"], rec["authorized_total_after"]) == ("PENDING_EVALUATION", "", 0)
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == 0


def test_overlapping_requests_same_agent_different_vendor_versions(funded):
    v2 = TERMS.replace("365 days", "180 days")
    funded.as_("owner").register_vendor_terms_version(1, v2)
    q2 = dict(Q, minimum_commitment_days="The initial subscription term is 180 days")
    r1, r2 = funded.new_request(version=1), funded.new_request(version=2)
    funded.evaluate(r2, model({"minimum_commitment_days": ("FOUND", 180, "NONE")}, terms_q=q2), audit(), v2)
    funded.evaluate(r1, model(), audit(), TERMS)
    a, b = funded.c.get_request(r1), funded.c.get_request(r2)
    assert (a["vendor_version"], b["vendor_version"]) == (1, 2)
    assert a["terms_digest"] != b["terms_digest"]
    assert "365 days" in a["findings_json"] and "180 days" not in a["findings_json"]
    assert "180 days" in b["findings_json"] and "365 days" not in b["findings_json"]
    assert f'"request_id":{r1}' in a["findings_json"] and f'"request_id":{r2}' in b["findings_json"]


def test_requests_of_two_agents_never_cross_link(funded):
    ra, rb = funded.new_request("alice"), funded.new_request("bob")
    with funded.vm.expect_revert("another agent"):
        funded.as_("bob").evaluate_purchase_request(ra, TERMS)
    funded.evaluate(rb, model({"data_retention_days": NOT_FOUND}), audit(), TERMS, who="bob")
    funded.evaluate(ra, model(), audit(), TERMS, who="alice")
    assert funded.c.get_request(ra)["status"] == "APPROVED" and funded.c.get_request(rb)["status"] == "REVIEW"
    assert funded.c.get_request(ra)["agent"] == funded.A["alice"].as_hex


def test_no_shared_latest_result(funded):
    r1 = funded.new_request()
    r2 = funded.new_request()
    funded.evaluate(r2, model({"automatic_renewal": ("FOUND", True, "NONE")}), audit(), TERMS)
    assert funded.c.get_request(r1)["status"] == "PENDING_EVALUATION"  # r2's result is not "latest" for r1
    funded.evaluate(r1, model(), audit(), TERMS)
    assert funded.c.get_request(r2)["status"] == "DENIED" and funded.c.get_request(r1)["status"] == "APPROVED"
