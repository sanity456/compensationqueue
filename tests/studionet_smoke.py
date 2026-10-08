"""Opt-in full StudioNet release flow with isolated DPAPI-protected wallets."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import time

from eth_account import Account
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from gltest.types import TransactionHashVariant, TransactionStatus
from gltest.utils import extract_contract_address
import requests
import win32crypt


ROOT = Path(__file__).resolve().parents[1]
RPC = "https://studio.genlayer.com/api"
ROLE_NAMES = ("owner", "operator", "verifier")


def wallet_store():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("This release test requires the current Windows user's LOCALAPPDATA")
    folder = Path(local_app_data) / "GenLayerStudioNetBatchA-20261008" / "compensationqueue"
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / (role + ".dpapi") for role in ROLE_NAMES]
    existing = [path.exists() for path in paths]
    if any(existing) and not all(existing):
        raise RuntimeError("Partial isolated wallet store; inspect it before continuing")
    accounts = {}
    for role, path in zip(ROLE_NAMES, paths):
        if path.exists():
            private_key = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1]
            accounts[role] = Account.from_key(private_key)
        else:
            account = Account.create()
            encrypted = win32crypt.CryptProtectData(bytes(account.key), "GenLayer CompensationQueue StudioNet", None, None, None, 0)
            path.write_bytes(encrypted)
            accounts[role] = account
    if len({account.address.lower() for account in accounts.values()}) != len(accounts):
        raise RuntimeError("Wallet collision")
    return folder, accounts


def rpc(method, params):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last_error = None
    for attempt in range(8):
        try:
            response = requests.post(RPC, json=payload, timeout=(10, 30))
            if response.status_code not in (429, 500, 502, 503, 504):
                response.raise_for_status()
                body = response.json()
                if body.get("error"):
                    raise AssertionError(json.dumps(body["error"], sort_keys=True))
                return body["result"]
            last_error = RuntimeError(f"StudioNet RPC returned HTTP {response.status_code}")
        except (requests.RequestException, ValueError) as error:
            last_error = error
        if attempt < 7:
            time.sleep(min(2 + attempt * 2, 12))
    raise AssertionError(f"StudioNet readback failed: {last_error}")


def receipt_hash(receipt):
    value = receipt.get("hash") or receipt.get("tx_id") or receipt.get("tx_hash") or receipt.get("transactionHash")
    assert isinstance(value, str) and value.startswith("0x"), json.dumps(receipt, default=str)
    return value


def test_studionet_reverse_stack_flow():
    wallet_folder, accounts = wallet_store()
    source_path = ROOT / "contracts" / "compensation_queue.py"
    source = source_path.read_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    local_abi = json.loads((ROOT / "contracts" / "abi.json").read_text(encoding="utf-8"))
    factory = get_contract_factory(contract_file_path=source_path)
    progress_path = wallet_folder / "deployment-progress.json"
    if progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        assert progress["source_sha256"] == source_hash
        address = progress["contract_address"]
        deployment_hash = progress["deployment_transaction_hash"]
    else:
        deployed = factory.deploy_contract_tx(args=[], account=accounts["owner"],
            wait_transaction_status=TransactionStatus.FINALIZED, wait_interval=10000, wait_retries=300)
        assert tx_execution_succeeded(deployed), json.dumps(deployed, default=str)
        address = extract_contract_address(deployed)
        deployment_hash = receipt_hash(deployed)
        progress_path.write_text(json.dumps({"contract_address": address,
            "deployment_transaction_hash": deployment_hash, "source_sha256": source_hash}, indent=2) + "\n", encoding="utf-8")

    assert rpc("eth_chainId", []) == "0xf22f"
    deployed_source = base64.b64decode(rpc("gen_getContractCode", [address]), validate=True)
    assert deployed_source == source
    assert rpc("gen_getContractSchema", [address]) == local_abi
    owner_contract = factory.build_contract(address, account=accounts["owner"])
    operator_contract = factory.build_contract(address, account=accounts["operator"])
    verifier_contract = factory.build_contract(address, account=accounts["verifier"])
    transactions = {"deploy": deployment_hash}

    def send(label, call):
        receipt = call.transact(wait_transaction_status=TransactionStatus.FINALIZED, wait_interval=10000, wait_retries=300)
        assert tx_execution_succeeded(receipt), json.dumps(receipt, default=str)
        transactions[label] = receipt_hash(receipt)

    case_key = "release-" + secrets.token_hex(4)
    context = "A community event reserved table T1 and chair set C1; there were no payments or other side effects."
    send("open_case", owner_contract.open_case(args=[case_key, context,
        accounts["operator"].address, accounts["verifier"].address]))
    case_id = accounts["owner"].address.lower() + ":" + case_key
    send("record_table", operator_contract.record_effect(args=[case_id,
        "Reserve table T1 for the event.",
        "One live reservation for table T1 exists under reference TAB-001; no other effect occurred.",
        "Cancel TAB-001, release table T1, and confirm no reservation remains."]))
    send("record_chairs", operator_contract.record_effect(args=[case_id,
        "Reserve chair set C1 for the event.",
        "One live reservation for chair set C1 exists under reference CHA-001; no other effect occurred.",
        "Cancel CHA-001, release chair set C1, and confirm no reservation remains."]))
    send("cancel", owner_contract.cancel(args=[case_id, "The event was cancelled."]))

    def state():
        return json.loads(owner_contract.get_case(args=[case_id]).call(transaction_hash_variant=TransactionHashVariant.LATEST_FINAL))

    send("review_table", owner_contract.review_next(args=[case_id]))
    assert state()["entries"][0]["assessment"] == "ADEQUATE", json.dumps(state(), sort_keys=True)
    send("review_chairs", owner_contract.review_next(args=[case_id]))
    reviewed = state()
    assert reviewed["status"] == "UNWINDING" and reviewed["undo_cursor"] == 1, json.dumps(reviewed, sort_keys=True)
    binding = reviewed["plan_hash"]
    send("begin_undo_chairs", operator_contract.begin_undo(args=[case_id, 1, binding]))
    send("verify_undo_chairs", verifier_contract.verify_undo(args=[case_id, 1, True,
        "CHA-001 was cancelled; chair set C1 was released and no reservation remains."]))
    assert state()["undo_cursor"] == 0
    send("begin_undo_table", operator_contract.begin_undo(args=[case_id, 0, binding]))
    send("verify_undo_table", verifier_contract.verify_undo(args=[case_id, 0, True,
        "TAB-001 was cancelled; table T1 was released and no reservation remains."]))
    final = state()
    assert final["status"] == "ROLLED_BACK" and final["undo_cursor"] == -1, json.dumps(final, sort_keys=True)

    record = {
        "schema": "genlayer-submission-evidence/v2",
        "network": {"name": "studionet", "chain_id": 61999, "rpc": RPC},
        "repository": "compensationqueue",
        "contract_address": address,
        "deployment_transaction_hash": deployment_hash,
        "wallet_addresses": {role: account.address for role, account in accounts.items()},
        "source_sha256": source_hash,
        "deployed_source_sha256": hashlib.sha256(deployed_source).hexdigest(),
        "source_bytes_match": True,
        "deployed_abi_matches_local": True,
        "abi_sha256": hashlib.sha256(json.dumps(local_abi, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "full_flow_test": {"status": "passed", "workflow_id": case_id, "final_state": "ROLLED_BACK",
            "transaction_hashes": transactions, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "test_file": "tests/studionet_smoke.py", "source_sha256": source_hash},
    }
    (ROOT / "deployments" / "studionet.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"contract_address": address, "deployment_transaction_hash": deployment_hash,
        "final_state": "ROLLED_BACK", "wallet_addresses": record["wallet_addresses"]}, sort_keys=True))
