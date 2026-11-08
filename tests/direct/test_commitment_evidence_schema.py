import copy
import json

import pytest

from .conftest import WEI
from .helpers import FIELDS, Q, TERMS, audit, fence, model, rows

DOMAIN = b"MandateGate/vendor-terms-commitment\x00"


def ref_digest(text: str) -> str:
    """Independent Keccak-256 (pycryptodome), NOT the SDK implementation."""
    from Crypto.Hash import keccak

    raw = text.encode("utf-8")
    pre = DOMAIN + (1).to_bytes(4, "big") + len(raw).to_bytes(8, "big") + raw
    return keccak.new(digest_bits=256, data=pre).hexdigest()


def err(fn, *a, msg="[LLM_ERROR]"):
    with pytest.raises(Exception) as ei:
        fn(*a)
    assert msg in str(ei.value), str(ei.value)


# ------------------------------------------------------------ source commitment
def test_sdk_keccak_matches_known_vector(funded):
    from genlayer.py.keccak import Keccak256

    assert Keccak256(b"").hexdigest() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert Keccak256(b"abc").hexdigest() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"


@pytest.mark.parametrize("text", [TERMS, "x", "é ü 日本語 🙂 ok", "a\r\nb\r\n", "é vs é", "A" * 8192])
def test_digest_matches_independent_reference(funded, text):
    got = funded.c.compute_terms_commitment(text)
    assert got["terms_digest"] == ref_digest(text)
    assert got["terms_byte_length"] == len(text.encode("utf-8"))
    assert got["hash_algorithm"] == "keccak256" and got["commitment_schema_version"] == 1


def test_digest_sensitivity(funded):
    d = lambda t: funded.c.compute_terms_commitment(t)["terms_digest"]
    base = d(TERMS)
    assert d(TERMS) == base
    assert d(TERMS.replace("365", "366")) != base  # one character
    assert d(TERMS + " ") != base  # one space
    assert d(TERMS.replace("\n", "\r\n")) != base  # line endings
    assert d("é") != d("é")  # no unicode normalisation
    assert d(TERMS.lower()) != base and d(TERMS.replace(".", ";")) != base


def test_registered_digest_equals_view_and_reference(funded):
    assert funded.c.get_vendor_version(1, 1)["terms_digest"] == ref_digest(TERMS)


def test_terms_size_and_type_limits(funded):
    o = funded.as_("owner")
    with funded.vm.expect_revert("empty"):
        o.register_vendor("")
    with funded.vm.expect_revert("MAX_TERMS_BYTES"):
        o.register_vendor("a" * 8193)
    with funded.vm.expect_revert("MAX_TERMS_BYTES"):
        o.register_vendor("é" * 4097)  # 8194 bytes, 4097 chars: bytes are what count
    o.register_vendor("é" * 4096)  # exactly 8192 bytes
    with funded.vm.expect_revert("utf-8"):  # rejected already by platform calldata encoding
        o.register_vendor("bad \ud800 surrogate")
    err(funded.mod._terms_bytes, "bad \ud800 surrogate", msg="not valid UTF-8")  # defence in depth
    err(funded.mod._terms_bytes, 5, msg="must be a string")


@pytest.mark.parametrize(
    "mutate,msg",
    [
        (lambda t: t.replace("365", "366"), "do not match registered commitment"),  # same length
        (lambda t: t + " ", "byte length"),
        (lambda t: t[:-1], "byte length"),
        (lambda t: t.replace("\n", "\r\n"), "byte length"),
        (lambda t: "Other vendor. " + t[14:], "do not match registered commitment"),
    ],
)
def test_wrong_source_rejected_before_any_llm_call(funded, mutate, msg):
    rid = funded.new_request()
    funded.vm.clear_mocks()  # any LLM call would raise MockNotFoundError instead of our message
    with funded.vm.expect_revert(msg):
        funded.as_("alice").evaluate_purchase_request(rid, mutate(TERMS))
    assert funded.c.get_request(rid)["status"] == "PENDING_EVALUATION"


def test_source_of_other_vendor_or_version_cannot_be_substituted(funded):
    o = funded.as_("owner")
    v2 = TERMS + "7. Extra. Nothing else.\n"
    o.register_vendor_terms_version(1, v2)
    o.register_vendor("Another vendor entirely.")
    rid = funded.new_request(version=1)
    rid2 = funded.new_request(vendor=1, version=2)
    funded.vm.clear_mocks()
    with funded.vm.expect_revert("match"):
        funded.as_("alice").evaluate_purchase_request(rid, v2)
    with funded.vm.expect_revert("match"):
        funded.as_("alice").evaluate_purchase_request(rid2, TERMS)
    with funded.vm.expect_revert("match"):
        funded.as_("alice").evaluate_purchase_request(rid, "Another vendor entirely.")


def test_same_text_registration_and_evaluation_succeeds(funded):
    assert funded.evaluate(funded.new_request(), model(), audit(), TERMS)["status"] == "APPROVED"


# --------------------------------------------------------------------- evidence
def resolve(mod, text, quote):
    return mod._resolve_quote(text, quote)


def test_quotes_in_terms_are_unique(funded):
    for q in Q.values():
        assert TERMS.count(q) == 1


def test_exact_quote_offsets(funded):
    s, e = resolve(funded.mod, TERMS, Q["data_retention_days"])
    assert TERMS[s:e] == Q["data_retention_days"] and e - s == len(Q["data_retention_days"])


@pytest.mark.parametrize(
    "quote,msg",
    [
        ("This is not in the document", "not found"),  # fabricated
        ("The initial subscription term is 366 days", "not found"),  # modified
        ("The initial term of the subscription is 365 days", "not found"),  # paraphrase
        ("the initial subscription term is 365 days", "not found"),  # case
        ("The initial subscription term is 365 days.", None),  # punctuation extends: still exact
        ("The  initial subscription term is 365 days", "not found"),  # double space
        (" Acme Cloud Terms of Service", "not found"),  # leading space is not in the source
        ("", "length"),
        ("x" * 601, "length"),
        (None, "string"),
        (365, "string"),
        (["a"], "string"),
    ],
)
def test_quote_resolution_cases(funded, quote, msg):
    if msg is None:
        resolve(funded.mod, TERMS, quote)
    else:
        err(resolve, funded.mod, TERMS, quote, msg=msg)


def test_trailing_and_leading_whitespace_are_exact(funded):
    text = "A. We keep data 30 days. \nB. Other.\n"
    resolve(funded.mod, text, "30 days. \nB")  # whitespace is part of the quote, kept exactly
    err(resolve, funded.mod, text, "30 days.\nB", msg="not found")


def test_repeated_quote_is_rejected(funded):
    text = "We keep data 30 days. We keep data 30 days. End."
    err(resolve, funded.mod, text, "We keep data 30 days.", msg="not unique")
    s, e = resolve(funded.mod, text, "days. We keep data 30 days. End")  # extended -> unique
    assert text[s:e] == "days. We keep data 30 days. End"


def test_overlapping_repeats_are_rejected(funded):
    err(resolve, funded.mod, "aaa", "aa", msg="not unique")  # overlapping occurrences at 0 and 1


@pytest.mark.parametrize(
    "prefix",
    ["", "é ", "🙂🙂 ", "日本語 ", "éé ", "👨‍👩‍👧 ", "😀 "[:0]],
)
def test_offsets_are_code_point_indices_with_unicode_before(funded, prefix):
    text = prefix + "We keep data 30 days after termination. Done."
    q = "We keep data 30 days after termination"
    s, e = resolve(funded.mod, text, q)
    assert s == len(prefix) and text[s:e] == q  # code points, not UTF-8 bytes
    assert len(text.encode()) >= len(text)


def test_crlf_versus_lf_is_not_normalised(funded):
    text = "Line one.\r\nWe keep data 30 days.\r\nEnd.\r\n"
    resolve(funded.mod, text, "one.\r\nWe keep")
    err(resolve, funded.mod, text, "one.\nWe keep", msg="not found")


def test_combining_characters_not_normalised(funded):
    text = "café data 30 days"
    err(resolve, funded.mod, text, "café data", msg="not found")
    resolve(funded.mod, text, "café data")


# ------------------------------------------------- fixed-position array schema
def parse(mod, text, terms=TERMS):
    return mod._findings_from_array(mod._parse_json_array(text), terms)


def mutated(fn):
    arr = [1, rows()]
    fn(arr)
    return fence(arr)


def test_valid_array_parses_and_offsets_are_code_computed(funded):
    f = parse(funded.mod, model())
    assert [x["field"] for x in f] == list(FIELDS)
    for x in f:
        for e in x["evidence"]:
            assert TERMS[e["start"]:e["end"]] == e["quote"]
    assert f[3]["days_value"] == 365 and f[0]["bool_value"] is False


def test_model_cannot_supply_offsets(funded):
    # Offsets are not part of the model schema: a quote object / extra element is rejected.
    bad = mutated(lambda a: a[1][0][5].__setitem__(0, {"start": 0, "end": 3, "quote": Q[FIELDS[0]]}))
    err(parse, funded.mod, bad, msg="quote must be a string")
    bad = mutated(lambda a: a[1][0].append(0))
    err(parse, funded.mod, bad, msg="six-element")


BAD_ARRAYS = {
    "bad_schema_version": lambda a: a.__setitem__(0, 2),
    "bool_schema_version": lambda a: a.__setitem__(0, True),
    "str_schema_version": lambda a: a.__setitem__(0, "1"),
    "extra_top_element": lambda a: a.append("APPROVED"),
    "five_rows": lambda a: a[1].pop(),
    "seven_rows": lambda a: a[1].append(copy.deepcopy(a[1][0])),
    "row_len_5": lambda a: a[1][0].pop(),
    "row_len_7": lambda a: a[1][0].append(None),
    "row_not_list": lambda a: a[1].__setitem__(0, {"field": "automatic_renewal"}),
    "swapped_order": lambda a: a[1].insert(0, a[1].pop(1)),
    "unknown_field": lambda a: a[1][2].__setitem__(0, "refund_policy"),
    "duplicate_field": lambda a: a[1][2].__setitem__(0, "customer_data_training"),
    "field_not_str": lambda a: a[1][2].__setitem__(0, 3),
    "lower_status": lambda a: a[1][0].__setitem__(1, "found"),
    "bad_status": lambda a: a[1][0].__setitem__(1, "MAYBE"),
    "status_not_str": lambda a: a[1][0].__setitem__(1, 1),
    "bool_as_string": lambda a: a[1][0].__setitem__(2, "false"),
    "bool_as_int": lambda a: a[1][0].__setitem__(2, 0),
    "bool_missing_null": lambda a: a[1][0].__setitem__(2, None),
    "bool_field_has_days": lambda a: a[1][0].__setitem__(3, 5),
    "days_as_string": lambda a: a[1][3].__setitem__(3, "365"),
    "days_as_float": lambda a: a[1][3].__setitem__(3, 365.0),
    "days_as_true": lambda a: a[1][3].__setitem__(3, True),
    "days_negative": lambda a: a[1][3].__setitem__(3, -1),
    "days_too_big": lambda a: a[1][3].__setitem__(3, 10**9 + 1),
    "days_field_has_bool": lambda a: a[1][3].__setitem__(2, False),
    "days_null_when_found": lambda a: a[1][3].__setitem__(3, None),
    "found_code_not_none": lambda a: a[1][0].__setitem__(4, "UNCLEAR_SCOPE"),
    "bad_code": lambda a: a[1][0].__setitem__(4, "WHATEVER"),
    "code_not_str": lambda a: a[1][0].__setitem__(4, None),
    "found_no_evidence": lambda a: a[1][0].__setitem__(5, []),
    "evidence_not_list": lambda a: a[1][0].__setitem__(5, "x"),
    "too_many_quotes": lambda a: a[1][0].__setitem__(5, [Q[f] for f in FIELDS[:5]]),
    "quote_not_str": lambda a: a[1][0].__setitem__(5, [5]),
    "duplicate_quote": lambda a: a[1][0].__setitem__(5, [Q[FIELDS[0]], Q[FIELDS[0]]]),
    "not_found_with_value": lambda a: a[1][0].__setitem__(1, "NOT_FOUND"),
    "not_found_with_evidence": lambda a: (a[1][0].__setitem__(1, "NOT_FOUND"), a[1][0].__setitem__(2, None)),
    "ambiguous_with_value": lambda a: (a[1][0].__setitem__(1, "AMBIGUOUS"), a[1][0].__setitem__(4, "UNCLEAR_SCOPE")),
    "ambiguous_code_none": lambda a: (a[1][0].__setitem__(1, "AMBIGUOUS"), a[1][0].__setitem__(2, None)),
    "ambiguous_no_evidence": lambda a: (a[1][0].__setitem__(1, "AMBIGUOUS"), a[1][0].__setitem__(2, None),
                                        a[1][0].__setitem__(4, "UNCLEAR_SCOPE"), a[1][0].__setitem__(5, [])),
    "not_found_code": lambda a: (a[1][0].__setitem__(1, "NOT_FOUND"), a[1][0].__setitem__(2, None),
                                 a[1][0].__setitem__(5, []), a[1][0].__setitem__(4, "OTHER_AMBIGUITY")),
}


@pytest.mark.parametrize("name", sorted(BAD_ARRAYS))
def test_malformed_model_arrays_are_rejected(funded, name):
    err(parse, funded.mod, mutated(BAD_ARRAYS[name]))


@pytest.mark.parametrize(
    "text",
    [
        "APPROVED",
        "I cannot help with that.",
        "",
        '{"decision":"APPROVED"}',
        '{"automatic_renewal":{},"automatic_renewal":{}}',
        "[",
        "]",
        "[1,",
        "[[[[[[[[[[",
        "[" * 100000 + "]" * 100000,
        "[1, NaN]",
        '[1, [["automatic_renewal"]]',
        "[]",
        "[1,[]]",
        "[1,{}]",
    ],
)
def test_non_array_or_garbage_output_is_rejected(funded, text):
    err(parse, funded.mod, text)


def test_non_text_model_output_rejected(funded):
    for v in (None, 5, {"a": 1}, [1, []], True):
        err(funded.mod._parse_json_array, v, msg="not text")


def test_tolerates_prose_and_fence_around_a_valid_array(funded):
    arr = json.dumps([1, rows()])
    for text in (arr, "```json\n" + arr + "\n```", "Sure! " + arr + " Hope it helps."):
        assert len(parse(funded.mod, text)) == 6


def test_explicit_zero_days_is_valid_and_distinct_from_missing(funded):
    f = parse(funded.mod, model({"data_retention_days": ("FOUND", 0, "NONE")}))
    assert f[5]["days_value"] == 0 and f[5]["status"] == "FOUND"
    f = parse(funded.mod, model({"data_retention_days": ("NOT_FOUND", None, "NONE")}))
    assert f[5]["days_value"] is None and f[5]["status"] == "NOT_FOUND"


def test_max_days_boundary(funded):
    parse(funded.mod, model({"data_retention_days": ("FOUND", 10**9, "NONE")}))
    err(parse, funded.mod, model({"data_retention_days": ("FOUND", 10**9 + 1, "NONE")}))


def test_multiple_and_conflicting_citations(funded):
    quotes = {"data_retention_days": [Q["data_retention_days"], Q["cancellation_notice_days"]]}
    f = parse(funded.mod, model(quotes=quotes))
    assert len(f[5]["evidence"]) == 2
    spec = {"data_retention_days": ("AMBIGUOUS", None, "CONFLICTING_CLAUSES")}
    f = parse(funded.mod, model(spec, quotes))
    assert f[5]["status"] == "AMBIGUOUS" and f[5]["ambiguity_code"] == "CONFLICTING_CLAUSES"


def test_ambiguity_codes_all_accepted(funded):
    for code in ("CONFLICTING_CLAUSES", "UNCLEAR_SCOPE", "UNDEFINED_PERIOD", "UNRESOLVED_REFERENCE", "OTHER_AMBIGUITY"):
        parse(funded.mod, model({"data_retention_days": ("AMBIGUOUS", None, code)}))
