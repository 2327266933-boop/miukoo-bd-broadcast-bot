import csv
import json
from typing import Any, Dict, List


IDENTITY_FIELDS = {
    "bd_id",
    "name",
    "contact_id",
    "mobile",
    "group",
    "variables_json",
}


def load_recipients_from_csv(path: str) -> List[Dict[str, Any]]:
    recipients: List[Dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError("CSV header is required")

        for row_number, row in enumerate(reader, start=2):
            normalized = {key: _clean(value) for key, value in row.items() if key}
            recipient = {
                "bd_id": normalized.get("bd_id") or normalized.get("contact_id") or normalized.get("mobile"),
                "name": normalized.get("name"),
                "contact_id": normalized.get("contact_id") or normalized.get("mobile"),
                "mobile": normalized.get("mobile"),
                "group": normalized.get("group"),
                "variables": _extract_variables(normalized, row_number),
            }
            recipients.append(recipient)

    return recipients


def _extract_variables(row: Dict[str, str], row_number: int) -> Dict[str, Any]:
    variables: Dict[str, Any] = {}
    variables_json = row.get("variables_json")
    if variables_json:
        try:
            decoded = json.loads(variables_json)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid variables_json at CSV row {}".format(row_number)) from exc
        if not isinstance(decoded, dict):
            raise ValueError("variables_json at CSV row {} must be an object".format(row_number))
        variables.update(decoded)

    for key, value in row.items():
        if key in IDENTITY_FIELDS or value in ("", None):
            continue
        if key.startswith("variable_"):
            variables[key[len("variable_"):]] = value
        else:
            variables[key] = value
    return variables


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
