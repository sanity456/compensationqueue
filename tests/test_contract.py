import json
from pathlib import Path

import pytest


@pytest.fixture
def setup(direct_vm, direct_deploy, direct_accounts):
    owner, operator, verifier = direct_accounts[:3]
    direct_vm.sender = owner
    source = Path(__file__).parents[1] / "contracts" / "compensation_queue.py"
    contract = direct_deploy(str(source), sdk_version="v0.2.16")
    case_id = contract.open_case("event-1", "A community event reserved table T1 and chair set C1.", operator, verifier)
    direct_vm.sender = operator
    contract.record_effect(case_id, "Reserve table T1", "A live booking for table T1 exists under TAB-001.", "Cancel TAB-001 and release table T1.")
    contract.record_effect(case_id, "Reserve chair set C1", "A live booking for chair set C1 exists under CHA-001.", "Cancel CHA-001 and release chair set C1.")
    return contract, case_id, (owner, operator, verifier)


def mock(vm, result):
    vm.clear_mocks()
    vm.mock_llm("CompensationQueue evaluates", json.dumps({"assessment": result}))


def review_to_unwinding(contract, case_id, owner, vm):
    vm.sender = owner
    contract.cancel(case_id, "The event was cancelled.")
    mock(vm, "ADEQUATE")
    assert contract.review_next(case_id) == "ADEQUATE"
    assert contract.review_next(case_id) == "ADEQUATE"
    state = json.loads(contract.get_case(case_id))
    assert state["status"] == "UNWINDING"
    return state["plan_hash"]


def test_reverse_order_and_verifier_rejection(setup, direct_vm):
    contract, case_id, (owner, operator, verifier) = setup
    binding = review_to_unwinding(contract, case_id, owner, direct_vm)
    assert contract.next_undo(case_id) == 1
    direct_vm.sender = operator
    with direct_vm.expect_revert("wrong undo order"):
        contract.begin_undo(case_id, 0, binding)
    contract.begin_undo(case_id, 1, binding)
    direct_vm.sender = verifier
    contract.verify_undo(case_id, 1, False, "CHA-001 remains booked; try again.")
    assert contract.next_undo(case_id) == 1
    direct_vm.sender = operator
    contract.begin_undo(case_id, 1, binding)
    direct_vm.sender = verifier
    contract.verify_undo(case_id, 1, True, "CHA-001 was cancelled and chair set C1 released.")
    assert contract.next_undo(case_id) == 0
    direct_vm.sender = operator
    contract.begin_undo(case_id, 0, binding)
    direct_vm.sender = verifier
    contract.verify_undo(case_id, 0, True, "TAB-001 was cancelled and table T1 released.")
    assert json.loads(contract.get_case(case_id))["status"] == "ROLLED_BACK"
    assert contract.next_undo(case_id) == -1


def test_insufficient_plan_can_be_revised_before_unwind(setup, direct_vm):
    contract, case_id, (owner, operator, _verifier) = setup
    direct_vm.sender = owner
    contract.cancel(case_id, "Event cancelled.")
    mock(direct_vm, "ADEQUATE")
    contract.review_next(case_id)
    mock(direct_vm, "INSUFFICIENT")
    assert contract.review_next(case_id) == "INSUFFICIENT"
    state = json.loads(contract.get_case(case_id))
    assert state["status"] == "REVIEWING" and state["review_cursor"] == 1
    direct_vm.sender = operator
    contract.revise_proposal(case_id, 1, "Cancel CHA-001 and release chair set C1; confirm no booking remains.")
    mock(direct_vm, "ADEQUATE")
    contract.review_next(case_id)
    assert json.loads(contract.get_case(case_id))["status"] == "UNWINDING"


def test_roles_freeze_and_stale_plan(setup, direct_vm, direct_accounts):
    contract, case_id, (owner, operator, verifier) = setup
    direct_vm.sender = direct_accounts[3]
    with direct_vm.expect_revert("owner"):
        contract.cancel(case_id, "Outsider cancellation.")
    binding = review_to_unwinding(contract, case_id, owner, direct_vm)
    direct_vm.sender = operator
    with direct_vm.expect_revert("recording"):
        contract.record_effect(case_id, "New action", "New effect", "New proposal")
    with direct_vm.expect_revert("stale plan"):
        contract.begin_undo(case_id, 1, "wrong")
    contract.begin_undo(case_id, 1, binding)
    direct_vm.sender = owner
    with direct_vm.expect_revert("verifier"):
        contract.verify_undo(case_id, 1, True, "I claim it was undone.")
    direct_vm.sender = verifier
    contract.verify_undo(case_id, 1, True, "CHA-001 was cancelled.")


def test_invalid_model_no_state_change_and_validator_rechecks(setup, direct_vm):
    contract, case_id, (owner, _operator, _verifier) = setup
    direct_vm.sender = owner
    contract.cancel(case_id, "Event cancelled.")
    before = contract.get_case(case_id)
    mock(direct_vm, "BAD")
    with direct_vm.expect_revert("invalid assessment"):
        contract.review_next(case_id)
    assert contract.get_case(case_id) == before
    mock(direct_vm, "ADEQUATE")
    contract.review_next(case_id)
    assert direct_vm.run_validator(leader_result="ADEQUATE")
    assert not direct_vm.run_validator(leader_result="INSUFFICIENT")
    assert not direct_vm.run_validator(leader_result={"assessment": "ADEQUATE"})


def test_distinct_roles_and_capacity(direct_vm, direct_deploy, direct_accounts):
    owner, operator, verifier = direct_accounts[:3]
    direct_vm.sender = owner
    source = Path(__file__).parents[1] / "contracts" / "compensation_queue.py"
    contract = direct_deploy(str(source), sdk_version="v0.2.16")
    with direct_vm.expect_revert("distinct"):
        contract.open_case("bad", "Context.", operator, operator)
    case_id = contract.open_case("capacity", "Context.", operator, verifier)
    direct_vm.sender = operator
    for i in range(5):
        contract.record_effect(case_id, f"Action {i}", f"Effect {i}", f"Reverse {i}")
    with direct_vm.expect_revert("capacity"):
        contract.record_effect(case_id, "Overflow", "Effect", "Reverse")
