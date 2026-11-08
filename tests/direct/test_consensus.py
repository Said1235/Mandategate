"""Validator-side consensus tests (CONS matrix).

Direct mode runs only the LEADER path. The captured validator is then replayed with
vm.run_validator(leader_result=...) using a leader result we tamper with, under the
mocks active at that moment (= what the validator's own LLM "sees").
"""
import copy

import pytest

from .conftest import WEI
from .helpers import FIELDS, NOT_FOUND, Q, TERMS, ambiguous, audit, fence, model, rows

ENV_CTX = ("schema_version", "request_id", "vendor_id", "vendor_version", "mandate_version",
           "terms_digest", "terms_byte_length")
MONTHS = "Acme Terms.\nThe initial term is 12 months.\nWe do not renew automatically.\n"


class Attack:
    """An honestly evaluated request whose captured validator we can replay."""

    def __init__(self, w, extract, audit_text, terms, rid):
        self.w, self.extract, self.audit, self.terms, self.rid = w, extract, audit_text, terms, rid
        self.idx = len(w.vm._captured_validators) - 1

    def env(self):
        return copy.deepcopy(self.w.vm._captured_validators[self.idx][0])

    def validate(self, env, extract="__keep__", audit_text="__keep__"):
        ex = self.extract if extract == "__keep__" else extract
        au = self.audit if audit_text == "__keep__" else audit_text
        self.w.mocks(ex, au)
        return self.w.vm.run_validator(leader_result=env, index=self.idx)

    def validator_fn(self):
        return self.w.vm._captured_validators[self.idx][2]


@pytest.fixture
def make_attack(funded):
    def make(spec=None, terms=TERMS, terms_q=None, quotes=None, vendor=None):
        w = funded
        ex, au = model(spec, quotes, terms_q), audit()
        if vendor is None:
            vendor = 1 if terms == TERMS else w.as_("owner").register_vendor(terms)
        rid = w.new_request(vendor=vendor)
        w.evaluate(rid, ex, au, terms)
        return Attack(w, ex, au, terms, rid)
    return make


@pytest.fixture
def atk(make_attack):
    return make_attack()


def set_nf(env, i):
    env["findings"][i].update(status="NOT_FOUND", bool_value=None, days_value=None,
                              ambiguity_code="NONE", evidence=[])


def flip(env, i):
    f = env["findings"][i]
    if i < 3:
        f["bool_value"] = not f["bool_value"]
    else:
        f["days_value"] += 1


# ------------------------------------------------------------------- CONS-001
def test_cons001_independent_identical_extraction_is_accepted(atk):
    assert atk.validate(atk.env()) is True
    assert atk.w.vm.run_validator(index=atk.idx) is True  # stored leader result, current mocks


# --------------------------------------------------------- CONS-002..007, 025
@pytest.mark.parametrize("i", range(6))
def test_cons002_007_one_field_value_changed_is_rejected(atk, i):
    env = atk.env()
    flip(env, i)
    assert atk.validate(env) is False


def test_cons025_integer_changed_inside_same_decision_category_is_rejected(atk):
    env = atk.env()
    env["findings"][4]["days_value"] = 29  # still compliant (<= 30): same APPROVED outcome
    assert atk.validate(env) is False
    env = atk.env()
    env["findings"][3]["days_value"] = 364
    assert atk.validate(env) is False


# --------------------------------------------------------- CONS-008..012
def test_cons008_leader_false_validator_true_rejected(atk):
    ex = model({"automatic_renewal": ("FOUND", True, "NONE")})
    assert atk.validate(atk.env(), extract=ex) is False


@pytest.mark.parametrize("i", range(6))
def test_cons009_leader_found_validator_not_found(atk, i):
    assert atk.validate(atk.env(), extract=model({FIELDS[i]: NOT_FOUND})) is False


@pytest.mark.parametrize("i", range(6))
def test_cons010_leader_found_validator_ambiguous(atk, i):
    assert atk.validate(atk.env(), extract=model({FIELDS[i]: ambiguous()})) is False


@pytest.mark.parametrize("i", range(6))
def test_cons011_leader_not_found_validator_finds_clause(atk, i):
    env = atk.env()
    set_nf(env, i)
    assert atk.validate(env) is False  # validator's own extraction finds the clause


@pytest.mark.parametrize("i", range(6))
def test_cons012_leader_not_found_validator_cannot_confirm_absence(atk, i):
    env = atk.env()
    set_nf(env, i)
    assert atk.validate(env, extract=model({FIELDS[i]: ambiguous()})) is False
    # validator agrees on NOT_FOUND but its audit sees a relevant clause / is unsure -> still rejected
    flags = [True] * 6
    flags[i] = False
    assert atk.validate(env, extract=model({FIELDS[i]: NOT_FOUND}), audit_text=audit(flags)) is False
    assert atk.validate(env, extract=model({FIELDS[i]: NOT_FOUND}), audit_text="I am not sure.") is False


def test_not_found_confirmed_by_independent_extraction_and_audit_is_accepted(make_attack):
    a = make_attack({"third_party_sharing": NOT_FOUND}, quotes={"third_party_sharing": []})
    assert a.validate(a.env()) is True  # absence is accepted only when both checks agree


# ------------------------------------------------------------------- CONS-013
def test_cons013_unsupported_unit_given_as_days_is_rejected(make_attack):
    q = dict(Q, minimum_commitment_days="The initial term is 12 months",
             automatic_renewal="We do not renew automatically")
    spec = {"minimum_commitment_days": ambiguous("UNDEFINED_PERIOD"), "cancellation_notice_days": NOT_FOUND,
            "customer_data_training": NOT_FOUND, "third_party_sharing": NOT_FOUND, "data_retention_days": NOT_FOUND}
    a = make_attack(spec, terms=MONTHS, terms_q=q, quotes={f: [] for f in spec if f != "minimum_commitment_days"})
    assert a.w.c.get_request(a.rid)["status"] == "REVIEW"
    env = a.env()
    env["findings"][3].update(status="FOUND", days_value=360, ambiguity_code="NONE")  # "12 months" -> 360 days
    assert a.validate(env) is False
    env["findings"][3]["days_value"] = 365
    assert a.validate(env) is False


# ------------------------------------------------------- CONS-014..017, 046
def test_cons014_fabricated_quote_is_rejected_by_deterministic_check(atk):
    env = atk.env()
    env["findings"][0]["evidence"][0].update(quote="We may renew at any time", end=env["findings"][0]["evidence"][0]["start"] + 24)
    assert atk.validate(env) is False
    env = atk.env()
    env["findings"][0]["evidence"] = [{"start": 0, "end": 4, "quote": "Fake"}]
    assert atk.validate(env) is False


def test_cons015_existing_quote_that_does_not_support_claim_is_blocked_by_audit(atk):
    env = atk.env()
    env["findings"][0]["evidence"] = copy.deepcopy(env["findings"][5]["evidence"])  # real text, wrong clause
    # Deterministic validation alone cannot see this (limitation documented in the contract) ...
    assert atk.w.mod._validate_envelope(env, _ctx_of(env), TERMS)["findings"][0]["evidence"]
    # ... the semantic audit does.
    flags = [False] + [True] * 5
    assert atk.validate(env, audit_text=audit(flags)) is False


def test_cons016_found_without_evidence_rejected(atk):
    for i in range(6):
        env = atk.env()
        env["findings"][i]["evidence"] = []
        assert atk.validate(env) is False


def test_cons017_quote_from_other_vendor_version_rejected(atk):
    env = atk.env()
    env["findings"][0]["evidence"] = [{"start": 10, "end": 33, "quote": "7. Extra. Nothing else."}]
    assert atk.validate(env) is False


def test_cons046_right_facts_wrong_evidence_source(atk):
    env = atk.env()
    ev = env["findings"][2]["evidence"][0]
    ev["start"] += 1  # offsets are checked against the source, never trusted
    ev["end"] += 1
    assert atk.validate(env) is False
    env = atk.env()
    env["findings"][2]["evidence"][0]["quote"] = "We never share customer data with third parties."  # extends, offsets stale
    assert atk.validate(env) is False


# ------------------------------------------------- CONS-018..020, 041, 047
def _ctx_of(env):
    return {k: env[k] for k in ENV_CTX}


@pytest.mark.parametrize("key", ENV_CTX)
@pytest.mark.parametrize("kind", ["plus_one", "string", "none", "float", "list", "bool"])
def test_cons018_020_041_047_every_envelope_value_is_bound(atk, key, kind):
    env = atk.env()
    old = env[key]
    if isinstance(old, str):
        env[key] = {"plus_one": old[:-1] + ("0" if old[-1] != "0" else "1"), "string": "x" * len(old), "none": None,
                    "float": 1.5, "list": [old], "bool": True}[kind]
    else:
        env[key] = {"plus_one": old + 1, "string": str(old), "none": None, "float": float(old), "list": [old],
                    "bool": bool(old)}[kind]
    if env[key] == old and type(env[key]) is type(old):
        pytest.skip("not a change")
    assert atk.validate(env) is False


def test_cons041_valid_digest_but_other_requests_id(make_attack, funded):
    a = make_attack()
    other = funded.new_request()
    env = a.env()
    env["request_id"] = other  # a real, existing request that shares the same source digest
    assert a.validate(env) is False


@pytest.mark.parametrize("key", ENV_CTX)
def test_envelope_key_missing_or_extra(atk, key):
    env = atk.env()
    del env[key]
    assert atk.validate(env) is False
    env = atk.env()
    env["decision"] = "APPROVED"
    assert atk.validate(env) is False


# ------------------------------------------------------- CONS-021..024, 026..030
def test_cons021_missing_field(atk):
    for i in range(6):
        env = atk.env()
        del env["findings"][i]
        assert atk.validate(env) is False
    env = atk.env()
    env["findings"] = []
    assert atk.validate(env) is False


def test_cons022_unknown_field_or_extra_element(atk):
    env = atk.env()
    env["findings"].append(copy.deepcopy(env["findings"][0]))
    assert atk.validate(env) is False
    env = atk.env()
    env["findings"][0]["refund_policy"] = "none"
    assert atk.validate(env) is False
    env = atk.env()
    env["findings"][0]["evidence"][0]["confidence"] = 0.99
    assert atk.validate(env) is False
    env = atk.env()
    env["findings"][2]["field"] = "refund_policy"
    assert atk.validate(env) is False


def test_cons023_duplicate_field_id_or_reorder(atk):
    env = atk.env()
    env["findings"][1] = copy.deepcopy(env["findings"][0])
    assert atk.validate(env) is False
    env = atk.env()
    env["findings"][0], env["findings"][1] = env["findings"][1], env["findings"][0]
    assert atk.validate(env) is False


def test_cons024_status_changed_other_values_preserved(atk):
    for new in ("NOT_FOUND", "AMBIGUOUS"):
        for i in range(6):
            env = atk.env()
            env["findings"][i]["status"] = new  # values/evidence kept -> structurally invalid
            assert atk.validate(env) is False
    env = atk.env()
    set_nf(env, 3)  # structurally valid NOT_FOUND, but the source says 365 days
    assert atk.validate(env) is False


TYPE_TAMPERS = {
    "cons026_str_for_int": (3, "days_value", "365"),
    "cons027_true_as_int": (3, "days_value", True),
    "cons026_float_for_int": (4, "days_value", 30.0),
    "cons028_int_for_bool": (0, "bool_value", 0),
    "cons028_str_for_bool": (0, "bool_value", "false"),
    "cons029_bad_code": (0, "ambiguity_code", "WHATEVER"),
    "cons029_code_on_found": (0, "ambiguity_code", "UNCLEAR_SCOPE"),
    "bad_status": (0, "status", "MAYBE"),
    "negative_days": (5, "days_value", -1),
    "bool_in_days_field": (3, "bool_value", False),
    "days_in_bool_field": (0, "days_value", 1),
    "evidence_not_list": (0, "evidence", "x"),
}


@pytest.mark.parametrize("name", sorted(TYPE_TAMPERS))
def test_cons026_029_wrong_types_rejected(atk, name):
    i, key, val = TYPE_TAMPERS[name]
    env = atk.env()
    env["findings"][i][key] = val
    assert atk.validate(env) is False


def test_cons029_unsupported_ambiguity_code_in_ambiguous_finding(make_attack):
    a = make_attack({"data_retention_days": ambiguous("UNCLEAR_SCOPE")})
    env = a.env()
    env["findings"][5]["ambiguity_code"] = "NONE"
    assert a.validate(env) is False
    env["findings"][5]["ambiguity_code"] = "MADE_UP_CODE"
    assert a.validate(env) is False


def test_ambiguity_code_is_part_of_the_comparison(make_attack):
    spec = {f: ambiguous("UNCLEAR_SCOPE") for f in FIELDS}
    a = make_attack(spec)
    assert a.validate(a.env()) is True
    for i in range(6):
        env = a.env()
        env["findings"][i]["ambiguity_code"] = "CONFLICTING_CLAUSES"
        assert a.validate(env) is False, FIELDS[i]
    other = model({f: ambiguous("UNDEFINED_PERIOD") for f in FIELDS})
    assert a.validate(a.env(), extract=other) is False


# ------------------------------------------------------------------- CONS-030
@pytest.mark.parametrize("i", range(6))
def test_cons030_found_without_evidence(atk, i):
    env = atk.env()
    env["findings"][i]["evidence"] = []
    assert atk.validate(env) is False


# ------------------------------------------------------------------- CONS-031
def test_cons031_different_quotes_same_facts_is_accepted(atk):
    own = model(quotes={"minimum_commitment_days": ["1. Term. The initial subscription term is 365 days"],
                        "cancellation_notice_days": ["3. Cancellation. You may cancel with 30 days written notice"]})
    assert atk.validate(atk.env(), extract=own) is True


def test_cons031_validator_still_audits_every_leader_quote(atk):
    own = model(quotes={"minimum_commitment_days": ["1. Term. The initial subscription term is 365 days"]})
    flags = [True, True, True, False, True, True]  # leader quote for field 4 unsupported
    assert atk.validate(atk.env(), extract=own, audit_text=audit(flags)) is False


# ------------------------------------------------------------------- CONS-032
def test_cons032_repeated_quote_cannot_be_uniquely_located(make_attack):
    doc = "Acme Terms.\nWe keep data 30 days.\nWe keep data 30 days.\nNo renewal. Cancel in 30 days.\n"
    q = {f: "Acme Terms" for f in FIELDS}
    spec = {f: NOT_FOUND for f in FIELDS}
    spec["data_retention_days"] = ("FOUND", 30, "NONE")
    quotes = {f: [] for f in FIELDS}
    quotes["data_retention_days"] = ["We keep data 30 days.\nWe keep data 30 days"]
    a = make_attack(spec, terms=doc, terms_q=q, quotes=quotes)
    env = a.env()
    env["findings"][5]["evidence"] = [{"start": 12, "end": 33, "quote": "We keep data 30 days."}]
    assert a.validate(env) is False
    # and the leader path refuses to produce such evidence at all
    rid = a.w.new_request(vendor=a.w.c.get_vendor_version(2, 1)["vendor_id"])
    a.w.mocks(model(spec, {**quotes, "data_retention_days": ["We keep data 30 days."]}, q), audit())
    with pytest.raises(Exception) as ei:
        a.w.as_("alice").evaluate_purchase_request(rid, doc)
    assert "not unique" in str(ei.value)


# ------------------------------------------------------- CONS-034, 035, 036, 037
def test_cons034_validator_llm_failure_means_no_agreement(atk):
    atk.w.vm.clear_mocks()  # validator's extraction call has no model available
    assert atk.w.vm.run_validator(leader_result=atk.env(), index=atk.idx) is False
    atk.w.vm.clear_mocks()
    atk.w.vm.mock_llm(r"TASK: EXTRACT", atk.extract)  # extraction ok, audit call unavailable
    assert atk.w.vm.run_validator(leader_result=atk.env(), index=atk.idx) is False


@pytest.mark.parametrize("junk", ["", "APPROVED", "Sorry, I cannot do that.", "[true]", "[true,true,true,true,true,'x']",
                                  fence([True] * 7), fence([1] * 6), fence(["true"] * 6), fence({"ok": True}),
                                  fence([True, True, True, True, True, None])])
def test_validator_audit_output_must_be_six_true_booleans(atk, junk):
    assert atk.validate(atk.env(), audit_text=junk) is False


@pytest.mark.parametrize("junk", ["", "APPROVED", fence({"decision": "APPROVED"}), fence([1, []]), "[1,[[]]]"])
def test_validator_garbage_extraction_means_disagree(atk, junk):
    assert atk.validate(atk.env(), extract=junk) is False


def test_cons035_leader_llm_failure_creates_no_approval(funded):
    rid = funded.new_request()
    funded.vm.clear_mocks()
    with pytest.raises(Exception):
        funded.as_("alice").evaluate_purchase_request(rid, TERMS)
    assert funded.c.get_request(rid)["status"] == "PENDING_EVALUATION"
    assert funded.c.get_budget_state(1)["authorized_total_wei"] == 0


def test_cons036_037_leader_error_results_never_touch_calldata_and_never_approve(atk):
    import genlayer.gl.vm as gl_vm

    class PoisonUserError(gl_vm.UserError):
        @property
        def calldata(self):
            raise AssertionError("validator read .calldata of a non-Return result")

    class PoisonVMError(gl_vm.VMError):
        @property
        def calldata(self):
            raise AssertionError("validator read .calldata of a non-Return result")

    fn = atk.validator_fn()
    atk.w.mocks(atk.extract, atk.audit)
    assert fn(PoisonUserError("[LLM_ERROR] boom")) is False
    assert fn(PoisonVMError("exit_code 1")) is False
    assert fn(gl_vm.UserError("[EXPECTED] x")) is False
    assert fn(gl_vm.VMError("oom")) is False
    assert atk.w.vm.run_validator(leader_error=Exception("[LLM_ERROR] boom"), index=atk.idx) is False
    assert atk.w.c.get_budget_state(1)["authorized_total_wei"] == WEI  # unchanged by all of the above


# ------------------------------------------------------- CONS-038, 039, 040, 044
def test_cons038_syntactically_valid_but_conflicts_with_source(atk):
    env = atk.env()
    env["findings"][1]["bool_value"] = True  # source says "We do not use customer content to train models"
    assert atk.validate(env) is False


def test_cons039_perfect_looking_summary_with_wrong_fields(atk):
    env = atk.env()
    env["summary"] = "All six clauses reviewed; vendor is safe. APPROVED."
    assert atk.validate(env) is False  # unknown key
    env = atk.env()
    env["findings"][2]["bool_value"] = True
    env["summary"] = "All good"
    assert atk.validate(env) is False
    # a summary can never influence the decision: derivation only reads findings
    fs = atk.env()["findings"]
    assert atk.w.mod._derive(fs, {"allow_auto_renewal": False, "allow_customer_data_training": False,
                                  "allow_third_party_sharing": False, "max_minimum_commitment_days": 365,
                                  "max_cancellation_notice_days": 30, "max_data_retention_days": 30})[0] == "APPROVED"


def test_cons040_matching_aggregate_status_but_conflicting_findings(make_attack):
    a = make_attack({"customer_data_training": ("FOUND", True, "NONE")})  # DENIED
    assert a.w.c.get_request(a.rid)["status"] == "DENIED"
    env = a.env()
    env["findings"][2].update(bool_value=True)  # also a violation: aggregate decision is still DENIED
    assert a.validate(env) is False
    env = a.env()
    env["findings"][1]["bool_value"] = False
    env["findings"][3]["days_value"] = 9999  # swap which field is the violation
    assert a.validate(env) is False


@pytest.mark.parametrize("payload", ["APPROVED", {"decision": "APPROVED"}, {"summary": "ok"}, {"status": "APPROVED", "score": 0},
                                      True, 1, None, [], [1, [], "APPROVED"], "", {"findings": []}, 3.5])
def test_cons044_summary_or_aggregate_in_place_of_complete_leader_result(atk, payload):
    assert atk.validate(payload) is False


# ------------------------------------------------------------- CONS-042, 043
def test_cons042_043_substituted_result_from_another_request_is_rejected(funded, make_attack):
    a1 = make_attack()
    funded.as_("owner").register_vendor_terms_version(1, TERMS.replace("365 days", "180 days"))
    v2 = TERMS.replace("365 days", "180 days")
    q2 = dict(Q, minimum_commitment_days="The initial subscription term is 180 days")
    rid2 = funded.new_request(version=2)
    ex2 = model({"minimum_commitment_days": ("FOUND", 180, "NONE")}, terms_q=q2)
    funded.evaluate(rid2, ex2, audit(), v2)
    a2 = Attack(funded, ex2, audit(), v2, rid2)
    assert a1.validate(a1.env()) is True and a2.validate(a2.env()) is True
    assert a1.validate(a2.env(), extract=a1.extract) is False  # result of request 2 offered for request 1
    assert a2.validate(a1.env(), extract=ex2) is False
    assert a1.validate(a2.env(), extract=ex2) is False  # even if the validator's own LLM agrees with it


# ------------------------------------------------------------------- CONS-045
@pytest.mark.parametrize("i", range(6))
@pytest.mark.parametrize("component", ["status", "value", "evidence_dropped", "audit_flag"])
def test_cons045_no_field_or_component_is_excluded_from_validation(atk, i, component):
    env = atk.env()
    ex, au = atk.extract, atk.audit
    if component == "status":
        set_nf(env, i)
    elif component == "value":
        flip(env, i)
    elif component == "evidence_dropped":
        env["findings"][i]["evidence"] = []
    else:
        flags = [True] * 6
        flags[i] = False
        au = audit(flags)
    assert atk.validate(env, audit_text=au) is False


def test_cons045_tamper_in_last_evidence_item_of_last_field_is_seen(make_attack):
    quotes = {"data_retention_days": [Q["data_retention_days"], Q["cancellation_notice_days"]]}
    a = make_attack(quotes=quotes)
    assert a.validate(a.env()) is True
    env = a.env()
    env["findings"][5]["evidence"][-1]["end"] -= 1
    assert a.validate(env) is False


def test_audit_prompt_contains_every_leader_quote_not_a_sample(atk):
    seen = []
    orig = atk.w.vm._match_llm_mock
    atk.w.vm._match_llm_mock = lambda p: (seen.append(p), orig(p))[1]
    assert atk.validate(atk.env()) is True
    audit_prompts = [p for p in seen if p.startswith("TASK: AUDIT")]
    extract_prompts = [p for p in seen if p.startswith("TASK: EXTRACT")]
    assert len(audit_prompts) == 1 and len(extract_prompts) == 1  # one independent extraction + one audit
    for q in Q.values():
        assert q in audit_prompts[0]
    assert TERMS in audit_prompts[0] and TERMS in extract_prompts[0]
    assert "[true,true,true,true,true,true]" in audit_prompts[0]


def test_leader_payload_is_calldata_encodable_and_complete(atk):
    from genlayer.py import calldata

    env = atk.env()
    assert calldata.decode(calldata.encode(env)) == env
    assert set(env) == set(ENV_CTX) | {"findings"} and len(env["findings"]) == 6
