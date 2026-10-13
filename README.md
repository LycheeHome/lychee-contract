# lychee-contract

The shared contract between the three repos that run the lychee host. It holds
what each side must agree on, in a form each side can check mechanically.

| Repo | Role |
| --- | --- |
| `lychee-ops` | Reconciles the host; produces the inventory and status files |
| `lychee-admin` | Web app; reads those files and writes declaration requests |
| `lychee-resources` | The declarations the reconciler runs |

## Files

- `schemas/` JSON Schemas for the inventory and the app and resource status files.
- `declarations/` the declaration validator (`validate_declarations.py`), its vocabulary and tests.
- `examples/` known-good and known-bad documents, checked by `tools/check_examples.py`.
- `constants.json` values every repo must share (domain, CI job name, ...).
- `sudo.json` the sudo grants the app depends on.
- `AGREEMENTS.md` what a schema cannot hold, each with a stable ID.
- `fetch-contract.sh` the reference fetch script consumers copy verbatim.

## Pinning and fetching

A consumer pins a release in `contract.lock` at its repo root:

```json
{"tag": "v1.0.0", "commit": "<40-hex sha of that tag>"}
```

and runs `./fetch-contract.sh`, which downloads that exact commit into
`.contract/` (skipping the download if `.contract/.commit` already matches) and
prints `contract: <tag> (<commit7>) from cache|download|LYCHEE_CONTRACT_DIR`.
It fails if the archive's commit differs from the pin.

- `LYCHEE_CONTRACT_DIR=<path>` uses a local checkout instead (warns if its HEAD differs from the pin; for development before a tag exists).
- `LYCHEE_CONTRACT_URL=<base>` overrides the archive base URL (for tests).

## The growing-only rule

Within a major version the contract only grows: see
[A-GROW-1](AGREEMENTS.md). Never remove or tighten something a consumer may
already depend on.

## Releasing

Tag `vX.Y.Z` on a commit that is green on `main`. A minor release adds; a major
release removes or tightens. Consumers move by editing `contract.lock` in their
own pull request.

## Checks

```
pip install -r requirements.txt
python -m unittest declarations.test_validate_declarations
python tools/check_examples.py
```
