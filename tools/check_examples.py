"""Check that every example declaration behaves as its directory says.

Run from anywhere:  python tools/check_examples.py   (needs PyYAML)

- examples/declarations/valid/ is validated together and must yield no errors.
- Each examples/declarations/invalid/<rule>.yml is validated ALONE in a temp
  dir (so a cross-file port clash cannot mask the rule under test) and must
  yield errors on exactly the `field` named by expected.json[<file>], and must
  not be in `valid`.

Exits non-zero, naming the file, on any failure.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "declarations" / "validate_declarations.py"
VOCAB = ROOT / "declarations" / "vocabulary.yml"
EXAMPLES = ROOT / "examples" / "declarations"
SITE_SUFFIX = "-lychee-land"


def validate(directory):
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), "--site-suffix", SITE_SUFFIX,
         str(directory), str(VOCAB)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"validator exited {proc.returncode}: {proc.stderr.strip()}")
    valid, errors = json.loads(proc.stdout)
    return valid, errors


def main():
    failures = []
    valid_dir = EXAMPLES / "valid"
    invalid_dir = EXAMPLES / "invalid"
    valid_files = sorted(valid_dir.glob("*.yml")) if valid_dir.is_dir() else []
    invalid_files = sorted(invalid_dir.glob("*.yml")) if invalid_dir.is_dir() else []
    if not valid_files or not invalid_files:
        print("check_examples: no examples found under examples/declarations/"
              "{valid,invalid}", file=sys.stderr)
        return 1

    try:
        _, errors = validate(valid_dir)
        for e in errors:
            failures.append(f"valid/: unexpected error on {e.get('name')}: "
                            f"{e.get('field')}: {e.get('message')}")
    except RuntimeError as exc:
        failures.append(f"valid/: {exc}")

    expected_path = invalid_dir / "expected.json"
    if not expected_path.is_file():
        failures.append("invalid/expected.json is missing")
        expected = {}
    else:
        expected = json.loads(expected_path.read_text())
    for name in sorted(set(expected) - {f.name for f in invalid_files}):
        failures.append(f"invalid/{name}: listed in expected.json but the file is missing")

    for f in invalid_files:
        if f.name not in expected:
            failures.append(f"invalid/{f.name}: no entry in expected.json")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            # A declaration's file stem must equal its `name`, so isolate the
            # rule under test by validating the file under its declared name;
            # only the name rule's own example keeps its mismatching filename.
            declared = (yaml.safe_load(f.read_text()) or {}).get("name")
            target = f.name if expected[f.name] == "name" else f"{declared}.yml"
            shutil.copy(f, pathlib.Path(tmp) / target)
            try:
                valid, errors = validate(tmp)
            except RuntimeError as exc:
                failures.append(f"invalid/{f.name}: {exc}")
                continue
        fields = [e.get("field") for e in errors]
        if set(fields) != {expected[f.name]}:
            failures.append(f"invalid/{f.name}: expected errors only on field "
                            f"{expected[f.name]!r}, got {fields}")
        if valid:
            failures.append(f"invalid/{f.name}: was accepted into `valid`")

    if failures:
        print("check_examples: FAILED", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"check_examples: ok ({len(valid_files)} valid, {len(invalid_files)} invalid)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
