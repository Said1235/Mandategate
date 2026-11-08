# MandateGate — Independent Audit Report (read-only)

Date: 2026-11-08. Audited contract: `contracts/mandate_gate.py`, sha256 `fd417219…bdb39`, 919 lines.

**Independence note.** The contract's author also produced this audit. To compensate:
1. A second agent audited the code without seeing the author's conclusions and wrote its own tests.
2. The author reproduced the key results: lint, the suite, 17 mutants and 16 own probe tests.

Both auditors are models of the same family. This does NOT replace a human auditor or a real-network test.

---

## A. Executive verdict

**PASS WITH LIMITATIONS** (only for logic, validator and accounting in Direct Mode).

- No CRITICAL or HIGH findings.
- It was not possible to make the contract store APPROVED or increase `authorized_total` when the leader's findings, evidence, source, request id or versions conflict with an honest validator.
- NOT verified: real-network consensus, `cloudpickle` in the real VM, real rollback, real-LLM behavior and loading in Studio. This is why it is not a full PASS.

## B. Repository and version information

| Item | Value |
|---|---|
| Git | Audited as a plain directory (not a git repository): no commit or branch. File hashes are in section L. |
| Deployment | None. Deployment verification: **NOT APPLICABLE**. |
| Source identities | (A) audited code: sha256 above. (B) deployed code: none exists. (C) vendor terms: committed per request with Keccak-256. Three different identities. |
| SDK / runner | Old generation `v0.2.16`, `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` |
| Tooling | genvm-linter 0.11.0, genlayer-test 0.29.2, pytest 9.2.0, Python 3.13 |
| Transaction context in GenVM v0.2.16 | `gl.message` exposes `contract_address`, `sender_address`, `origin_address`, `value`, `chain_id`. **There is no transaction hash.** The contract does not try to use one. |

## C. Findings summary

| CRITICAL | HIGH | MEDIUM | LOW | INFO |
|---|---|---|---|---|
| 0 | 0 | 0 | 6 | 4 |

## D. Detailed findings

All line numbers refer to `mandate_gate.py`.

**L-01 (LOW) — Semantic relevance of evidence rests only on the LLM audit.**
- Where: `_resolve_quote` (165-176), `_check_finding` (179-243), `validator_fn` (802-812).
- Cause: the code requires a quote to exist exactly once. It requires no minimum length and no relevance. `_decision_view` (302-307) does not include evidence.
- Scenario (demonstrated by the second agent): a leader with all six values correct cites unrelated text, or `"Z"`. The validator accepts it if the second (audit) LLM answers true for that field.
- Expected: a deterministic backstop, or an audit that rejects it. Observed: only the LLM stops it. Not a bypass against an honest validator, and consistent with the spec's design.
- Fix: minimum quote length and/or a deterministic check that the quote contains the number or negation it claims.
- Test: `test_evidence_swap_same_decision_view_accepted_only_by_audit` and `test_one_char_quote_is_accepted_as_evidence` from the second agent (scratch copy).

**L-02 (LOW) — A REVOKED authorization still shows `decision == "APPROVED"`.**
- Where: 683 (`revoke_authorization` changes only `status`), 829, view `get_request` (856-873).
- Scenario: a consumer reading `decision` instead of `status` treats a revoked authorization as live.
- Fix: document it, or clear/rename `decision` on revoke. Spec line 1673 (do not decrement the total) is correctly followed.

**L-03 (LOW) — Failures after the LLM call revert the transaction and leave no REVIEW record.**
- Where: 171-175, 208, 229-231, 768, 825.
- Cases: a repeated clause with no unique quote, days > 10^9, or a request whose approval would exceed the cap (it cannot end DENIED or REVIEW; the call only reverts).
- Spec: line 1525 asks for REVIEW on "unsupported/insufficiently evidenced". Line 1948 allows some failures to leave the request PENDING if documented.
- Observed: the header only says "fail closed" and does not list which failures leave PENDING. No approval risk (fails closed, budget untouched).
- Fix: document a "failure → PENDING" vs "business outcome → REVIEW/DENIED" table.

**L-04 (LOW) — A leader error (UserError/VMError) can never reach agreement.**
- Where: 803-804 and SDK `gl/vm.py` lines 235-257 (`run_nondet`).
- Verified in the SDK source:
  - the validator runs in a sandbox and returns `False` (a `Return`);
  - `type(answer) is not type(leaders_result)` then yields `False`;
  - to agree on an error the validator would have to re-raise the same `UserError`; the official docs confirm ("compares the error against the leader's error").
- Impact: liveness and diagnostics. A malformed leader LLM output leads to disagreement, rotation and, at worst, `undetermined`, which does not modify state. No approval risk.
- Not tested on a real network.

**L-05 (LOW) — Test gap: post-consensus revalidation (line 817) is not covered by the suite.**
- Mutant `env = result` survives the 384 non-mutation tests.
- Cause: Direct Mode always returns the leader's honest output.
- Mitigation done during the audit: 9 own tests that patch `gl.vm.run_nondet` with tampered envelopes (request_id, digest, mandate, FOUND without evidence, forged quote, 5 findings, extra key, non-dict, None). All are rejected and state is unchanged. They live in the auditor's scratch copy, not in this repo.
- Fix: add those tests to `tests/direct`.
- A second mutant (removing the explicit `isinstance(Return)` check) also survives. It is behaviorally equivalent: `.calldata` raises AttributeError, which `except Exception` turns into False. Keeping the explicit check is still better for clarity.

**L-06 (LOW, documentation) — The header does not say APPROVED is provisional until Finalized.**
- Official docs (finality): "an accepted result is provisional"; a successful appeal can force recomputation of non-finalized transactions. An Accepted receipt can also contain a UserError.
- The contract claims neither unanimity nor irreversibility, but it does not warn either.
- Fix: add that warning to the header and README.

**I-01 (INFO) — The digest does not include vendor_id, version or request_id.**
- Preimage: domain ‖ schema ‖ length ‖ bytes (129-137).
- The same text under two vendors gives the same digest.
- Binding is done by the storage key and the consensus envelope. The second agent showed that replaying a valid result for another request, vendor, version or mandate is rejected by the validator.
- Spec §7 asks for linkage to those ids. It holds through the envelope and storage, not through the hash.

**I-02 (INFO) — Approvals survive revocation of the agent, mandate or vendor.** Matches the spec wording ("future evaluations"). There is no live `is_authorized` view.

**I-03 (INFO) — An active agent can create unlimited PENDING requests.** A revoked agent can still cancel its own.

**I-04 (INFO) — `MAX_TERMS_BYTES = 8192` was not measured on a network.** The header admits this.

### Potential risks (not confirmed defects)

- **Liveness.** Acceptance requires exact equality between two LLM runs, including the ambiguity code. It also requires unique, literal quotes. An honest leader can be rejected with real models. It fails closed, but can be annoying. Could not be measured.
- **Prompt injection.** Residual risk: if leader and validator are fooled the same way, consensus can accept a wrong result.
  - Mitigations present: the closing marker uses `digest[:24]`, which an attacker cannot predict because it depends on the text itself; independent re-extraction; a second audit prompt.
  - There are 34 injection tests. They do not exercise a real LLM.

## E. Historical failure-pattern matrix

| Historical failure pattern | Implementation location | Result | Evidence | Test |
|---|---|---|---|---|
| Validator checks only overall status | `validator_fn` 802-812 | **PASS** | Compares `_decision_view` of all 6 fields (302-307) | "decision-only" mutant killed; "skip field i" mutants ×6 killed |
| Conflicting FOUND/NOT_FOUND findings accepted | 808 | **PASS** | Exact status comparison | `test_cons002_007`, status mutation |
| Result not completely bound | `_validate_envelope` 288-299 | **PASS** | 7 context keys with exact type and value | "skip request_id" mutant killed; 9 post-consensus tampering probes |
| Score tolerance hides threshold changes | — | **PASS / N/A** | No scores, buckets or tolerances (grep) | "day tolerance" mutant killed |
| Risk bucket replaces verification | — | **N/A** | Does not exist | — |
| Source commitment absent | `_commitment` 129-137, 771-781 | **PASS** | Keccak-256 with domain and version; checked before any LLM; matches an independent vector | "no digest" mutant killed; added space, CRLF, changed digit, changed Unicode character and empty text rejected before any LLM |
| Validators lack the exact source | closures 795-812 | **PASS (Direct) / NOT VERIFIED (network)** | The text travels in the closure, not just the hash | Pickling watched; real cloudpickle not verified |
| Findings not re-examined | 807 | **PASS** | Own re-extraction | CONS matrix |
| Summaries/recommendations contradict findings | — | **N/A** | The contract stores no LLM summaries or recommendations; `reasons_json` is derived in code (310-338) | — |
| Wallet-list diff identifies the record | — | **PASS** | No such logic | code review |
| Result not tied to request_id | 744-832 | **PASS** | `request_id` enters as an argument and through `ctx` | reverse-order evaluation of 3 requests (own probes) |
| Overlapping requests cross-link | 535, 722-741, 828-832 | **PASS** | `requests: TreeMap[u256,…]`, monotonic ids | `test_three_requests_reverse_order_no_crosslink` |
| "Latest result" replaces keyed history | — | **PASS** | No `latest_*` variable | grep |
| Missing becomes false/zero | `_derive` 324-338 | **PASS** | `status != FOUND` is always incomplete | "NOT_FOUND as false" and "AMBIGUOUS as compliant" mutants killed; 729 combinations |
| Evidence not semantically validated | `_audit` 446-454 | **PASS WITH LIMITATIONS** | Only the audit LLM (see L-01) | second agent's probes |
| Accepted described as successful/final | header 1-46 | **PASS WITH LIMITATIONS** | Claims neither unanimity nor finality, but gives no warning (L-06) | documentation review |

## F. Six-field matrix

The structure of verification is identical for each field: leader extraction, validator comparison, code-resolved evidence and a decision rule.

| Field | Leader extraction | Independent validator check | Evidence | Decision | Regression test |
|---|---|---|---|---|---|
| automatic_renewal | yes | yes (compares status, bool, code) | unique literal quote; relevance via LLM | `_derive` 329 | "skip field" mutant killed; flip rejected |
| customer_data_training | yes | yes | same | 329 | same |
| third_party_sharing | yes | yes | same | 329 | same |
| minimum_commitment_days | yes | yes (exact integer; ±1 rejected) | same | 332 | same + "tolerance" mutant killed |
| cancellation_notice_days | yes | yes | same | 332 | same |
| data_retention_days | yes | yes | same | 332 | same |

Types: `bool` does not pass as an integer (111-112). Floats and strings are rejected. NOT_FOUND and AMBIGUOUS require null values (210-219).

## G. Request identity and history

- **Id:** `request_count + 1`, assigned by the contract in `create_purchase_request` (722-741). A per-agent nonce prevents replays.
- **Selection:** `evaluate_purchase_request(request_id, terms_text)` requires an active agent, ownership of the request and PENDING state.
- **Validator binding:** the leader's envelope includes `request_id`, vendor, versions and digest. Each validator compares it with its own `ctx`.
- **Persistence:** the result is written to `requests[request_id]` (828-832), after all checks.
- **Overlap:** tested with 3 requests from 2 agents evaluated in order 3, 1, 2. Each result lands under its own id with no shared reference.
- **History:** every request is retrievable with `get_request(id)`, and terminal states are not overwritten.
- **Receipt identity:** GenVM v0.2.16 does not expose the transaction hash to the contract. Associating a receipt with a `request_id` can only be done off-chain, using returned values. The contract does not claim otherwise. **Not verifiable on-chain.**

## H. Test execution report

| Command | Result | What it proves | What it does NOT prove |
|---|---|---|---|
| `genvm-lint check contracts/mandate_gate.py --json` | ok, 17 methods (6 view, 11 write) | Reflection and schema against runner `1jb45…` | That Studio will load the contract |
| `pytest tests/direct` | **399 passed** | Logic with a mocked LLM in Direct Mode | Real-network consensus |
| Own mutation run (17 mutants) | 15 killed, 2 survive | Tests detect removed validations | See L-05 |
| Own probes (16 tests) | **16 passed** | Identity, ordering, cap, retries, source, access, post-consensus | Real network |
| Second agent: probes + suite | 469 passed (399 + 70); mutation 24/29 killed | 46,656-case decision oracle, Unicode, access | Not re-run by the author |

The second agent's results (the 46,656-case oracle, Unicode, 25 malformed outputs, 29 mutants) are reported as **not re-executed by the author**.

**Real-network evidence: none.** Direct Mode with mocks does not prove network consensus.

## I. Official source verification

- **Equivalence Principle (read during the audit):**
  - the validator receives a `gl.vm.Result` (`Return | UserError | VMError`) and must verify the answer, not its shape;
  - with `run_nondet` (sandbox) a validator error is compared with the leader's error;
  - an accepted result is not claimed to be correct.
- **Installed SDK v0.2.16 (source read):** `run_nondet` is the sandboxed variant and `run_nondet_unsafe` the generic one. The name `run_nondet_default` mentioned in the audit brief belongs to SDK v0.3, which is neither used nor installed. Usage is consistent with the installed version.
- **Finality (read during the audit):** Accepted is provisional, an appeal can force recomputation, and an Accepted receipt can contain an error (see L-06).
- **Not re-read in this audit:** non-determinism, calling-llms, prompt-injection, storage, error-handling, protocol docs and skills. Those were checked earlier in the project, not during this audit.

## J. Unverified items

- Loading and execution in Studio / StudioNet.
- Consensus with several validators and real LLMs.
- Real `cloudpickle` serialization in the VM and real rollback semantics. The design is atomic by write ordering: nothing is written before consensus.
- Maximum viable terms size and gas cost.
- Deployment and source-to-deployed-contract provenance: no deployment exists.

## K. Remediation plan (not applied)

1. **L-05:** add the 9 post-consensus tampering tests to `tests/direct`. Expected: all revert without touching state.
2. **L-01:** minimum quote length and/or a deterministic check that the quote contains the claimed value. Tests: 1-character quote, quote from another field.
3. **L-06 and L-03:** document provisional finality and the PENDING vs REVIEW/DENIED table.
4. **L-02:** clear or rename `decision` on revoke, or document it. Test `get_request` after revoke.
5. **L-04:** decide whether to accept the current liveness or re-raise the leader's error when the validator reproduces the same one. Only evaluable on a network.
6. Test on StudioNet with an explicit instruction from the owner and a real LLM, measuring `MAX_TERMS_BYTES`.

## L. Scope confirmation

"Only the existing MandateGate GenLayer Intelligent Contract, its tests, and its contract-specific documentation were audited. No frontend, backend, API application, website, dashboard, mobile app, or dApp was created."

"No original source files were modified during the initial read-only audit." Verified: the sha256 of the 14 project files before and after were **identical** (`diff` produced no output). Tests and mutations ran on copies in a scratch directory. Nothing was deployed and no network was touched.
