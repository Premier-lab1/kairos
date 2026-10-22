"""Validate the files in examples/ against the JSON Schemas in schemas/.

    pip install jsonschema
    python schemas/validate.py
"""

import json
import pathlib
import sys

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAIRS = {
    "signal.json": "signal.schema.json",
    "token.json": "token.schema.json",
    "shiller.json": "shiller.schema.json",
    "mention.json": "mention.schema.json",
    "outcome.json": "outcome.schema.json",
}


def main() -> int:
    failed = 0
    for example, schema_name in PAIRS.items():
        schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        document = json.loads((ROOT / "examples" / example).read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda e: list(e.path))
        if errors:
            failed += 1
            print(f"FAIL examples/{example} against schemas/{schema_name}")
            for error in errors:
                print("  ", "/".join(map(str, error.path)) or "<root>", "-", error.message)
        else:
            print(f"ok   examples/{example} against schemas/{schema_name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
