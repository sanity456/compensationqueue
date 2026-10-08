# CompensationQueue

CompensationQueue coordinates the reversal of effects that were already recorded. An owner appoints an operator and a distinct verifier. The operator records up to five effects in occurrence order and proposes a reversal for each. When the owner cancels the case, GenLayer validators independently review whether each proposal would address the full recorded effect. An insufficient or uncertain proposal can be revised before any undo begins.

After every proposal is accepted, the contract freezes a plan hash and enforces undo in last-in, first-out order. The operator starts each reversal; the verifier attests success or sends it back for another attempt. Only verified reversals advance the stack. The case becomes `ROLLED_BACK` only after every recorded effect has been handled.

## Distinct mechanism

This contract has no dependency graph, prerequisite unlocking, forward-step evidence screening, or hash-chain build closure. Its consensus question is whether a proposed reversal covers a recorded effect. Its deterministic core is a reverse stack with separate operator and verifier roles.

## Evidence and trust boundary

Actions, effects, proposals, and verifier notes are public declarations. The contract does not execute a reversal or verify a physical result. A verifier wallet is not a professional credential. No funds or assets are held. Do not submit secrets or private records.

## Verify

The source pins a concrete GenVM runner. Use GenVM version `v0.2.16` for the linter, then run `genvm-lint check contracts/compensation_queue.py`, `genvm-lint typecheck contracts/compensation_queue.py --strict`, and `python -m pytest tests/test_contract.py -q`.

`tests/studionet_smoke.py` is an explicit live test. It creates three fresh wallets under the current Windows user's DPAPI protection outside this repository, deploys to StudioNet chain 61999, exercises review and reverse-order verification through full consensus, checks the final state, reads back byte-identical source and ABI, and writes transaction evidence to `deployments/studionet.json` only after success. Do not run it as a routine unit test. The submitted Explorer address must be the `contract_address` in that evidence file.

## Interface

Writes: `open_case`, `record_effect`, `cancel`, `review_next`, `revise_proposal`, `begin_undo`, `verify_undo`. Views: `get_case`, `next_undo`. Cases are scoped to the owner's wallet and key.

## Live StudioNet evidence

- [Explorer contract](https://explorer-studio.genlayer.com/address/0x336Ccf72B24042d09C6A0ab9D1A1c22542E9d4b0)
- [Studio import](https://studio.genlayer.com/?import-contract=0x336Ccf72B24042d09C6A0ab9D1A1c22542E9d4b0)
- [Deployment transaction](https://explorer-studio.genlayer.com/tx/0x9cd658e3678b872cccb2b9286bf4fbde1542a3f05dec228078866d1b54392a72)
- [Intelligent plan-review transaction](https://explorer-studio.genlayer.com/tx/0x3e1472cf2e9ee442edf541735df2cf63aab8b204e568cf133b7a69f47720120d)
- [Full transaction manifest](deployments/studionet.json)

The live flow reached `ROLLED_BACK` on 2026-10-08. The read-only release proof re-fetches the deployed code, ABI, deployment calldata, and every recorded receipt before submission. See [audit notes](AUDIT.md).
