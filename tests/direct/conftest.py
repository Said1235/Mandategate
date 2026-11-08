import os
import sys

import pytest

# Old-generation runner (py-genlayer:1jb45...) lives in the GenVM v0.2.16 bundle.
os.environ.setdefault("GENVM_VERSION", "v0.2.16")

CONTRACT = "contracts/mandate_gate.py"
MODULE = "_contract_mandate_gate"
WEI = 10**18


class World:
    """Deployed contract + role helpers. Fixture addresses are raw bytes, so contract
    arguments are wrapped in Address once the SDK is on sys.path (after deploy)."""

    def __init__(self, vm, contract, raw, Address, module=MODULE):
        self.module = module
        self.vm = vm
        self.c = contract
        self._raw = raw
        self.A = {k: Address(v) for k, v in raw.items()}
        self.Address = Address
        self.nonces = {}

    def as_(self, who):
        self.vm.sender = self._raw[who]
        return self.c

    @property
    def mod(self):
        return sys.modules[self.module]

    # -- scenario helpers -------------------------------------------------
    def new_request(self, who="alice", amount=WEI, vendor=1, version=1, mandate=1):
        c = self.as_(who)
        nonce = c.get_agent(self.A[who])["next_nonce"]
        return c.create_purchase_request(vendor, version, mandate, amount, nonce)

    def mocks(self, extract, audit):
        self.vm.clear_mocks()
        if extract is not None:
            self.vm.mock_llm(r"TASK: EXTRACT", extract)
        if audit is not None:
            self.vm.mock_llm(r"TASK: AUDIT", audit)

    def evaluate(self, rid, extract, audit, terms, who="alice"):
        self.mocks(extract, audit)
        self.as_(who).evaluate_purchase_request(rid, terms)
        return self.c.get_request(rid)


@pytest.fixture
def world(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob, direct_charlie):
    direct_vm.sender = direct_owner
    direct_vm.check_pickling = True
    c = direct_deploy(CONTRACT)
    from genlayer.py.types import Address

    raw = {"owner": direct_owner, "alice": direct_alice, "bob": direct_bob, "charlie": direct_charlie}
    return World(direct_vm, c, raw, Address)


def fund(world):
    """alice+bob agents; mandate v1 (2 GEN/purchase, 3 GEN total, nothing risky allowed,
    365/30/30 day limits); vendor 1 version 1 = TERMS."""
    from .helpers import TERMS

    c = world.as_("owner")
    c.register_agent(world.A["alice"])
    c.register_agent(world.A["bob"])
    c.create_mandate_version(2 * WEI, 3 * WEI, False, False, False, 365, 30, 30)
    c.register_vendor(TERMS)
    return world


@pytest.fixture
def funded(world):
    return fund(world)
