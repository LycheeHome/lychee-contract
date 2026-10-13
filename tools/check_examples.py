"""Check the contract's machine-checkable parts against their examples.

Run from anywhere:  python tools/check_examples.py   (needs requirements.txt)

Declarations (the validator, not a schema):
- examples/declarations/valid/ is validated together and must yield no errors.
- Each examples/declarations/invalid/<rule>.yml is validated ALONE in a temp
  dir (so a cross-file port clash cannot mask the rule under test) and must
  yield errors on exactly the `field` named by expected.json[<file>], and must
  not be in `valid`.

Schemas (JSON Schema 2020-12, schemas/*.schema.json):
- every schema passes the 2020-12 meta-schema;
- every examples/<shape>/valid/*.json validates against schemas/<shape>.schema.json;
- every examples/<shape>/invalid/*.json fails, and every top-level error sits at
  the JSON Pointer that invalid/expected.json names for it (an error inside
  oneOf is reported at the oneOf's own location, so that is the pointer).

Data files:
- constants.json has exactly the agreed keys;
- sudo.json command ids are unique;
- every `## A-...` heading in AGREEMENTS.md is unique.

Exits non-zero, naming the file, on any failure.
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

import re

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = pathlib.Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "declarations" / "validate_declarations.py"
VOCAB = ROOT / "declarations" / "vocabulary.yml"
EXAMPLES = ROOT / "examples" / "declarations"
SITE_SUFFIX = "-lychee-land"
SCHEMAS = ROOT / "schemas"
# shape directory under examples/ -> its schema
SHAPES = ("inventory", "app-status", "resource-status")
CONSTANT_KEYS = frozenset((
    "ci_job", "domain", "site_suffix", "site_name_pattern",
    "max_resource_name_length", "image_prefix", "tag_pattern", "floating_tags",
    "release_tag_pattern", "reserved_ports", "site_min_port", "paths",
))
AGREEMENT_HEADING = re.compile(r"^## (A-[A-Z]+-[0-9]+)\b", re.MULTILINE)


def validate(directory):
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), "--site-suffix", SITE_SUFFIX,
         str(directory), str(VOCAB)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"validator exited {proc.returncode}: {proc.stderr.strip()}")
    valid, errors = json.loads(proc.stdout)
    return valid, errors


def pointer(path):
    """A jsonschema error path as an RFC 6901 JSON Pointer."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def check_schemas(failures):
    schema_files = sorted(SCHEMAS.glob("*.schema.json")) if SCHEMAS.is_dir() else []
    if not schema_files:
        failures.append("schemas/: no *.schema.json found")
        return 0
    schemas = {}
    for f in schema_files:
        try:
            schema = json.loads(f.read_text())
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # malformed JSON or a meta-schema violation
            # a SchemaError's str() is a page of meta-schema trace; its message is the point
            failures.append(f"schemas/{f.name}: {getattr(exc, 'message', exc)}")
            continue
        schemas[f.name] = schema
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values() if "$id" in s)

    checked = 0
    for shape in SHAPES:
        name = f"{shape}.schema.json"
        if name not in schemas:
            failures.append(f"schemas/{name}: missing")
            continue
        validator = Draft202012Validator(schemas[name], registry=registry)
        base = ROOT / "examples" / shape
        valid_files = sorted((base / "valid").glob("*.json"))
        invalid_files = sorted((base / "invalid").glob("*.json"))
        invalid_files = [f for f in invalid_files if f.name != "expected.json"]
        if not valid_files or not invalid_files:
            failures.append(f"examples/{shape}/: needs both valid/ and invalid/ examples")
            continue
        for f in valid_files:
            for e in validator.iter_errors(json.loads(f.read_text())):
                failures.append(f"examples/{shape}/valid/{f.name}: "
                                f"{pointer(e.absolute_path) or '/'}: {e.message}")
        expected_path = base / "invalid" / "expected.json"
        expected = json.loads(expected_path.read_text()) if expected_path.is_file() else {}
        if not expected_path.is_file():
            failures.append(f"examples/{shape}/invalid/expected.json is missing")
        for missing in sorted(set(expected) - {f.name for f in invalid_files}):
            failures.append(f"examples/{shape}/invalid/{missing}: listed in "
                            "expected.json but the file is missing")
        for f in invalid_files:
            if f.name not in expected:
                failures.append(f"examples/{shape}/invalid/{f.name}: no entry in expected.json")
                continue
            got = sorted({pointer(e.absolute_path)
                          for e in validator.iter_errors(json.loads(f.read_text()))})
            if got != [expected[f.name]]:
                failures.append(f"examples/{shape}/invalid/{f.name}: expected errors only "
                                f"at {expected[f.name]!r}, got {got}")
        checked += len(valid_files) + len(invalid_files)
    return checked


def check_data_files(failures):
    try:
        constants = json.loads((ROOT / "constants.json").read_text())
        keys = set(constants)
        if keys != CONSTANT_KEYS:
            failures.append(f"constants.json: missing {sorted(CONSTANT_KEYS - keys)}, "
                            f"unexpected {sorted(keys - CONSTANT_KEYS)}")
    except (OSError, ValueError) as exc:
        failures.append(f"constants.json: {exc}")

    try:
        ids = [c["id"] for c in json.loads((ROOT / "sudo.json").read_text())["commands"]]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            failures.append(f"sudo.json: duplicate command ids {dupes}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        failures.append(f"sudo.json: {exc!r}")

    try:
        headings = AGREEMENT_HEADING.findall((ROOT / "AGREEMENTS.md").read_text())
        if not headings:
            failures.append("AGREEMENTS.md: no `## A-...` headings")
        dupes = sorted({h for h in headings if headings.count(h) > 1})
        if dupes:
            failures.append(f"AGREEMENTS.md: duplicate agreement ids {dupes}")
    except OSError as exc:
        failures.append(f"AGREEMENTS.md: {exc}")


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

    schema_examples = check_schemas(failures)
    check_data_files(failures)

    if failures:
        print("check_examples: FAILED", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"check_examples: ok (declarations: {len(valid_files)} valid, "
          f"{len(invalid_files)} invalid; schema examples: {schema_examples})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
