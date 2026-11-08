"""Mutation tests: the suite must be able to tell a weak validator / engine from the real one.

Each mutant is a copy of the contract with ONE deliberate weakening. `battery()` runs a set of
attacks against a deployed contract and returns the names of those that SUCCEEDED (i.e. the
contract behaved insecurely). The real contract must yield []; every mutant must yield at
least its expected attack. This is how CONS-045 / CONS-048 ("a validator that checks only the
overall status must make the suite fail") are demonstrated rather than merely claimed.
"""
import copy
from pathlib import Path

import pytest

from .conftest import CONTRACT, WEI, World, fund
from .helpers import FIELDS, NOT_FOUND, Q, TERMS, ambiguous, audit, model
from .test_consensus import Attack

REPEAT_DOC = "Acme Terms.\nWe keep data 30 days.\nWe keep data 30 days.\nNo renewal. Cancel in 30 days.\n"


BIG = 2  # battery runs under a roomy mandate so a weakened engine cannot hide behind the cap


def honest(w, spec=None, terms=TERMS, terms_q=None, quotes=None):
    vendor = 1 if terms == TERMS else w.as_("owner").register_vendor(terms)
    ex, au = model(spec, quotes, terms_q), audit()
    rid = w.new_request(vendor=vendor, mandate=BIG)
    w.evaluate(rid, ex, au, terms)
    return Attack(w, ex, au, terms, rid)


def battery(w):
    bad = []
    w.as_("owner").create_mandate_version(100 * WEI, 100 * WEI, False, False, False, 365, 30, 30)

    def accepted(name, atk, env, **kw):
        if atk.validate(env, **kw) is True:
            bad.append(name)

    a = honest(w)
    env = a.env(); env["findings"][0]["bool_value"] = not env["findings"][0]["bool_value"]
    accepted("value_tamper", a, env)
    env = a.env(); env["findings"][4]["days_value"] = 29
    accepted("day_tamper", a, env)
    env = a.env(); env["findings"][0]["evidence"] = copy.deepcopy(env["findings"][5]["evidence"])
    accepted("audit_ignored", a, env, audit_text=audit([False] + [True] * 5))
    env = a.env(); env["request_id"] += 1
    accepted("request_id", a, env)
    env = a.env(); env["terms_digest"] = "0" * 64
    accepted("digest", a, env)
    env = a.env(); env["findings"][2]["evidence"][0]["start"] += 1; env["findings"][2]["evidence"][0]["end"] += 1
    accepted("offsets", a, env)
    env = a.env(); env["findings"][0]["evidence"] = [{"start": 0, "end": 4, "quote": "Fake"}]
    accepted("fabricated_quote", a, env)
    env = a.env(); env["summary"] = "safe"
    accepted("extra_key", a, env)
    env = a.env(); env["findings"][3]["evidence"] = []
    accepted("found_without_evidence", a, env)
    accepted("summary_only", a, "APPROVED")
    env = a.env(); env["findings"][3].update(status="NOT_FOUND", days_value=None, ambiguity_code="NONE", evidence=[])
    accepted("status_change", a, env)  # structurally valid NOT_FOUND where the source says 365 days

    b = honest(w, {"customer_data_training": ("FOUND", True, "NONE")})  # DENIED already
    env = b.env(); env["findings"][2]["bool_value"] = True  # still DENIED: aggregate-only agreement
    accepted("aggregate_only", b, env)

    c = honest(w, {"third_party_sharing": ("FOUND", True, "NONE")})
    env = c.env(); env["findings"][2]["bool_value"] = 1  # int where bool is required (1 == True)
    accepted("bool_as_int", c, env)

    d = honest(w, {f: ambiguous("UNCLEAR_SCOPE") for f in FIELDS})
    env = d.env(); env["findings"][0]["ambiguity_code"] = "CONFLICTING_CLAUSES"
    accepted("ambiguity_code", d, env)

    q = {f: "Acme Terms" for f in FIELDS}
    spec = {f: NOT_FOUND for f in FIELDS}; spec["data_retention_days"] = ("FOUND", 30, "NONE")
    quotes = {f: [] for f in FIELDS}; quotes["data_retention_days"] = ["We keep data 30 days.\nWe keep data 30 days"]
    e = honest(w, spec, REPEAT_DOC, q, quotes)
    env = e.env(); env["findings"][5]["evidence"] = [{"start": 12, "end": 33, "quote": "We keep data 30 days."}]
    accepted("repeated_quote", e, env)

    # decision engine / accounting / commitment behaviour
    rid = w.new_request(mandate=BIG)
    r = w.evaluate(rid, model({"third_party_sharing": NOT_FOUND}, quotes={"third_party_sharing": []}), audit(), TERMS)
    if r["status"] != "REVIEW":
        bad.append("review_decision")
    before = w.c.get_budget_state(BIG)["authorized_total_wei"]
    rid = w.new_request(mandate=BIG)
    w.evaluate(rid, model({"data_retention_days": ("FOUND", 31, "NONE")}), audit(), TERMS)
    if w.c.get_request(rid)["status"] != "DENIED":
        bad.append("day_boundary")
    rid = w.new_request(mandate=BIG)
    r = w.evaluate(rid, model({"cancellation_notice_days": NOT_FOUND}, quotes={"cancellation_notice_days": []}), audit(), TERMS)
    if w.c.get_budget_state(BIG)["authorized_total_wei"] != before:
        bad.append("review_counts")
    rid = w.new_request(mandate=BIG)
    w.mocks(model(), audit())
    try:  # same length, edit OUTSIDE every cited clause: only the source commitment can stop it
        w.as_("alice").evaluate_purchase_request(rid, TERMS.replace("Acme Cloud", "Acme Clown"))
        bad.append("wrong_text_same_length")
    except Exception:
        pass
    return bad


def mutate(src, old, new):
    assert src.count(old) == 1, old
    return src.replace(old, new)


MUTANTS = {
    "no_field_comparison": ('if _decision_view(leader_env["findings"]) != _decision_view(own):', "if False:",
                            {"value_tamper", "day_tamper", "status_change", "ambiguity_code"}),
    "aggregate_only_comparison": ('if _decision_view(leader_env["findings"]) != _decision_view(own):',
                                  'if _derive(leader_env["findings"], mandate)[0] != _derive(own, mandate)[0]:',
                                  {"aggregate_only"}),
    "no_audit": ('return _audit(terms_text, leader_env["findings"], tag)', "return True", {"audit_ignored"}),
    "no_envelope_binding": ("if type(env[k]) is not type(ctx[k]) or env[k] != ctx[k]:", "if False:",
                            {"request_id", "digest"}),
    "envelope_extra_keys_allowed": ('if type(env) is not dict or set(env.keys()) != set(ENV_CTX_KEYS + ("findings",)):',
                                    "if type(env) is not dict:", {"extra_key"}),
    "offsets_trusted": ("if rs != s or rt != t or terms_text[s:t] != q:", "if False:", {"offsets"}),
    "bool_accepts_int": ("if type(bv) is not bool or dv is not None:", "if dv is not None:", {"bool_as_int"}),
    "ambiguity_code_ignored": ('f["days_value"], f["ambiguity_code"]]', 'f["days_value"]]', {"ambiguity_code"}),
    "repeated_quote_allowed": ("if terms_text.find(quote, start + 1) >= 0:", "if False:", {"repeated_quote"}),
    "found_needs_no_evidence": ("if len(ev) < 1:\n            _fail(E_LLM, \"FOUND needs evidence: \" + name)", "pass",
                                {"found_without_evidence"}),
    "review_approves": ("return ST_REVIEW, incomplete", "return ST_APPROVED, []", {"review_decision"}),
    "off_by_one_limit": ('if f["days_value"] > limit[i - N_BOOL]:', 'if f["days_value"] > limit[i - N_BOOL] + 1:',
                         {"day_boundary"}),
    "review_consumes_budget": ("if decision == ST_APPROVED:\n            total_after = total_after + amount",
                               "if decision != ST_DENIED:\n            total_after = total_after + amount",
                               {"review_counts"}),
    "commitment_not_enforced": ('_fail(E_EXPECTED, "terms do not match registered commitment")', "pass",
                                {"wrong_text_same_length"}),
}


def deploy_world(direct_vm, direct_deploy, accounts, path):
    direct_vm.sender = accounts["owner"]
    direct_vm.check_pickling = True
    c = direct_deploy(str(path))
    from genlayer.py.types import Address

    return fund(World(direct_vm, c, accounts, Address, module="_contract_" + Path(path).stem))


@pytest.fixture
def accounts(direct_owner, direct_alice, direct_bob, direct_charlie):
    return {"owner": direct_owner, "alice": direct_alice, "bob": direct_bob, "charlie": direct_charlie}


def test_real_contract_survives_every_attack(direct_vm, direct_deploy, accounts):
    w = deploy_world(direct_vm, direct_deploy, accounts, Path(CONTRACT).resolve())
    assert battery(w) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_each_weakened_contract_is_caught_by_the_suite(direct_vm, direct_deploy, accounts, tmp_path, name):
    old, new, expected = MUTANTS[name]
    src = mutate(Path(CONTRACT).read_text(), old, new)
    path = tmp_path / f"mut_{name}.py"
    path.write_text(src)
    w = deploy_world(direct_vm, direct_deploy, accounts, path)
    caught = set(battery(w))
    assert caught, f"mutant {name} survived: the suite cannot see this weakness"
    assert expected <= caught, (expected, caught)
