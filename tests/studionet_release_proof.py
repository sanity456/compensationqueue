"""Read-only StudioNet gate for the exact source, ABI, and flow receipts."""
import base64
import hashlib
import json
from pathlib import Path
import time

import requests


ROOT = Path(__file__).resolve().parents[1]
RPC = "https://studio.genlayer.com/api"


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


def test_exact_submission_deployment_and_flow():
    manifest = json.loads((ROOT / "deployments" / "studionet.json").read_text(encoding="utf-8"))
    source = (ROOT / "contracts" / "compensation_queue.py").read_bytes()
    abi = json.loads((ROOT / "contracts" / "abi.json").read_text(encoding="utf-8"))
    source_hash = hashlib.sha256(source).hexdigest()
    address = manifest["contract_address"]
    assert rpc("eth_chainId", []) == "0xf22f"
    assert manifest["network"]["chain_id"] == 61999
    assert manifest["source_sha256"] == source_hash == manifest["deployed_source_sha256"]
    assert manifest["full_flow_test"]["source_sha256"] == source_hash
    assert manifest["full_flow_test"]["status"] == "passed"
    assert manifest["full_flow_test"]["final_state"] == "ROLLED_BACK"
    assert len({address.lower() for address in manifest["wallet_addresses"].values()}) == len(manifest["wallet_addresses"])
    assert base64.b64decode(rpc("gen_getContractCode", [address]), validate=True) == source
    assert rpc("gen_getContractSchema", [address]) == abi

    hashes = manifest["full_flow_test"]["transaction_hashes"]
    assert len(hashes) == 11 and len(set(hashes.values())) == 11
    assert hashes["deploy"] == manifest["deployment_transaction_hash"]
    deployment = rpc("eth_getTransactionByHash", [hashes["deploy"]])
    assert deployment["to_address"].lower() == address.lower()
    assert base64.b64decode(deployment["data"]["contract_code"], validate=True) == source
    for label, transaction_hash in hashes.items():
        transaction = rpc("eth_getTransactionByHash", [transaction_hash])
        receipt = rpc("eth_getTransactionReceipt", [transaction_hash])
        assert transaction is not None and receipt is not None, label
        assert transaction["to_address"].lower() == address.lower(), label
        assert receipt["to"].lower() == address.lower(), label
        assert receipt["status"] == "0x1", label
