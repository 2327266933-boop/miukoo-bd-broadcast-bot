import csv
import json
import time
from typing import Any, Dict, Iterable, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from miukoo_bot.config import Settings


class MerchantLookupError(ValueError):
    pass


MERCHANT_NAME_FIELDS = (
    "merchant_name",
    "shop_name",
    "poi_name",
    "store_name",
    "商家名称",
    "门店名称",
    "POI名称",
)
BD_ID_FIELDS = ("bd_id", "bd_user_id", "BDID", "bd编号")
BD_NAME_FIELDS = ("bd_name", "name", "owner_name", "BD", "bd姓名", "BD姓名")
CONTACT_ID_FIELDS = ("contact_id", "open_id", "user_id", "mobile", "手机号")
GROUP_FIELDS = ("group", "region", "city_group", "区域", "分组", "城市")


class MerchantBDLookup:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cached_access_token = ""
        self._cached_access_token_expires_at = 0.0

    def lookup(self, raw_merchant_names: Any) -> Dict[str, Any]:
        merchant_names = split_merchant_names(raw_merchant_names)
        if not merchant_names:
            raise MerchantLookupError("merchant_names must be a non-empty list")

        provider = (self.settings.merchant_bd_lookup_provider or "csv").lower()
        if provider not in ("csv", "fengshen", "auto"):
            raise MerchantLookupError(
                "Unsupported merchant lookup provider: {}".format(provider)
            )

        if provider in ("fengshen", "auto") and self.settings.fengshen_merchant_bd_lookup_url:
            result = self._lookup_from_fengshen(merchant_names)
        elif provider == "fengshen":
            raise MerchantLookupError("FENGSHEN_MERCHANT_BD_LOOKUP_URL is required")
        else:
            result = self._lookup_from_csv(merchant_names)

        result["recipients"] = build_recipients_from_lookup_results(result["results"])
        result["recipient_count"] = len(result["recipients"])
        return result

    def _lookup_from_csv(self, merchant_names: List[str]) -> Dict[str, Any]:
        path = self.settings.merchant_bd_mapping_csv
        if not path:
            rows = []
        else:
            with open(path, newline="", encoding="utf-8-sig") as csv_file:
                rows = list(csv.DictReader(csv_file))
        return build_lookup_response("csv", merchant_names, rows)

    def _lookup_from_fengshen(self, merchant_names: List[str]) -> Dict[str, Any]:
        payload = json.dumps({"merchant_names": merchant_names}, ensure_ascii=False)
        headers = {"Content-Type": "application/json"}
        access_token = self._resolve_fengshen_access_token()
        if access_token:
            headers["Authorization"] = "Bearer {}".format(access_token)

        request = Request(
            self.settings.fengshen_merchant_bd_lookup_url,
            data=payload.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.fengshen_timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise MerchantLookupError(
                "Fengshen lookup failed: HTTP {}".format(exc.code)
            ) from exc
        except URLError as exc:
            raise MerchantLookupError("Fengshen lookup failed: {}".format(exc)) from exc
        except json.JSONDecodeError as exc:
            raise MerchantLookupError("Fengshen lookup returned invalid JSON") from exc

        if not isinstance(decoded, dict):
            raise MerchantLookupError("Fengshen lookup response must be a JSON object")

        if "results" in decoded:
            raw_results = decoded["results"]
        elif isinstance(decoded.get("data"), dict) and "results" in decoded["data"]:
            raw_results = decoded["data"]["results"]
        else:
            raw_results = decoded.get("data")

        if (
            isinstance(raw_results, list)
            and raw_results
            and isinstance(raw_results[0], dict)
            and "matches" in raw_results[0]
        ):
            results = [
                normalize_result_item(item, merchant_names[index] if index < len(merchant_names) else "")
                for index, item in enumerate(raw_results)
            ]
            return summarize_results("fengshen", merchant_names, results)

        if not isinstance(raw_results, list):
            raise MerchantLookupError(
                "Fengshen lookup response must include results or data list"
            )
        return build_lookup_response("fengshen", merchant_names, raw_results)

    def _resolve_fengshen_access_token(self) -> str:
        if self.settings.fengshen_api_token:
            return self.settings.fengshen_api_token

        has_client_credentials = (
            self.settings.fengshen_token_url
            and self.settings.fengshen_client_id
            and self.settings.fengshen_client_secret
        )
        if not has_client_credentials:
            partially_configured = any(
                (
                    self.settings.fengshen_token_url,
                    self.settings.fengshen_client_id,
                    self.settings.fengshen_client_secret,
                )
            )
            if partially_configured:
                raise MerchantLookupError(
                    "FENGSHEN_TOKEN_URL, FENGSHEN_CLIENT_ID and "
                    "FENGSHEN_CLIENT_SECRET are required together"
                )
            return ""

        now = time.time()
        if (
            self._cached_access_token
            and self._cached_access_token_expires_at
            and now < self._cached_access_token_expires_at - 60
        ):
            return self._cached_access_token

        payload = {
            self.settings.fengshen_client_id_field: self.settings.fengshen_client_id,
            self.settings.fengshen_client_secret_field: (
                self.settings.fengshen_client_secret
            ),
        }
        if self.settings.fengshen_scope:
            payload["scope"] = self.settings.fengshen_scope

        request = Request(
            self.settings.fengshen_token_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.fengshen_timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise MerchantLookupError(
                "Fengshen token request failed: HTTP {}".format(exc.code)
            ) from exc
        except URLError as exc:
            raise MerchantLookupError(
                "Fengshen token request failed: {}".format(exc)
            ) from exc
        except json.JSONDecodeError as exc:
            raise MerchantLookupError(
                "Fengshen token response returned invalid JSON"
            ) from exc

        token, expires_in = extract_access_token(decoded)
        if not token:
            raise MerchantLookupError(
                "Fengshen token response must include access_token or token"
            )

        self._cached_access_token = token
        self._cached_access_token_expires_at = now + expires_in
        return token


def split_merchant_names(value: Any) -> List[str]:
    if isinstance(value, str):
        raw_items = (
            value.replace("，", "\n")
            .replace("、", "\n")
            .replace("；", "\n")
            .replace(";", "\n")
            .replace(",", "\n")
            .splitlines()
        )
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    names = []
    seen = set()
    for item in raw_items:
        name = str(item).strip()
        key = normalize_name(name)
        if name and key not in seen:
            names.append(name)
            seen.add(key)
    return names


def build_lookup_response(
    source: str,
    merchant_names: List[str],
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized_rows = [
        normalize_match(row) for row in rows if row and isinstance(row, dict)
    ]
    results = []
    for merchant_name in merchant_names:
        matches = find_matches(merchant_name, normalized_rows)
        if not matches:
            results.append(
                {
                    "merchant_name": merchant_name,
                    "status": "not_found",
                    "matches": [],
                    "message": "No BD found for merchant",
                }
            )
        elif len(matches) == 1:
            results.append(
                {
                    "merchant_name": merchant_name,
                    "status": "matched",
                    "matches": matches,
                    "message": "",
                }
            )
        else:
            results.append(
                {
                    "merchant_name": merchant_name,
                    "status": "ambiguous",
                    "matches": matches,
                    "message": "Multiple BD records matched this merchant",
                }
            )

    return summarize_results(source, merchant_names, results)


def summarize_results(
    source: str,
    merchant_names: List[str],
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    matched_count = sum(1 for result in results if result["status"] == "matched")
    ambiguous_count = sum(1 for result in results if result["status"] == "ambiguous")
    unmatched_count = sum(1 for result in results if result["status"] == "not_found")
    return {
        "source": source,
        "total": len(merchant_names),
        "matched_count": matched_count,
        "ambiguous_count": ambiguous_count,
        "unmatched_count": unmatched_count,
        "results": results,
    }


def find_matches(
    merchant_name: str,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    target = normalize_name(merchant_name)
    exact = [
        row for row in rows if normalize_name(row.get("merchant_name")) == target
    ]
    if exact:
        return exact

    partial = []
    for row in rows:
        row_name = normalize_name(row.get("merchant_name"))
        if target and row_name and (target in row_name or row_name in target):
            partial.append(row)
    return partial


def normalize_result_item(item: Dict[str, Any], fallback_name: str) -> Dict[str, Any]:
    merchant_name = clean(item.get("merchant_name") or item.get("query") or fallback_name)
    raw_matches = item.get("matches") or []
    if not isinstance(raw_matches, list):
        raw_matches = []
    matches = [normalize_match(match, merchant_name) for match in raw_matches if match]
    if matches:
        status = "matched" if len(matches) == 1 else "ambiguous"
    else:
        status = item.get("status") or "not_found"
    return {
        "merchant_name": merchant_name,
        "status": status,
        "matches": matches,
        "message": item.get("message") or "",
    }


def normalize_match(row: Dict[str, Any], fallback_merchant_name: str = "") -> Dict[str, Any]:
    cleaned = {str(key).strip(): clean(value) for key, value in row.items()}
    merchant_name = first_value(cleaned, MERCHANT_NAME_FIELDS) or fallback_merchant_name
    bd_id = first_value(cleaned, BD_ID_FIELDS)
    name = first_value(cleaned, BD_NAME_FIELDS)
    contact_id = first_value(cleaned, CONTACT_ID_FIELDS)
    group = first_value(cleaned, GROUP_FIELDS)
    variables = {
        key: value
        for key, value in cleaned.items()
        if value
        and key
        not in set(
            MERCHANT_NAME_FIELDS
            + BD_ID_FIELDS
            + BD_NAME_FIELDS
            + CONTACT_ID_FIELDS
            + GROUP_FIELDS
        )
    }
    variables["merchant_name"] = merchant_name
    return {
        "merchant_name": merchant_name,
        "bd_id": bd_id,
        "name": name,
        "contact_id": contact_id,
        "group": group,
        "variables": variables,
    }


def extract_access_token(payload: Dict[str, Any]) -> Tuple[str, int]:
    candidates = [payload]
    if isinstance(payload.get("data"), dict):
        candidates.append(payload["data"])
    if isinstance(payload.get("result"), dict):
        candidates.append(payload["result"])

    for item in candidates:
        token = clean(
            item.get("access_token")
            or item.get("token")
            or item.get("accessToken")
            or item.get("jwtToken")
            or item.get("jwt_token")
            or item.get("jwt")
        )
        if token:
            expires_in = item.get("expires_in") or item.get("expiresIn") or 3600
            try:
                parsed_expires_in = int(expires_in)
            except (TypeError, ValueError):
                parsed_expires_in = 3600
            return token, max(parsed_expires_in, 120)

    return "", 0


def build_recipients_from_lookup_results(
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for result in results:
        if result["status"] != "matched":
            continue
        match = result["matches"][0]
        if not match.get("bd_id") or not match.get("name") or not match.get("contact_id"):
            continue
        key = (match["bd_id"], match["contact_id"])
        if key not in grouped:
            grouped[key] = {
                "bd_id": match["bd_id"],
                "name": match["name"],
                "contact_id": match["contact_id"],
                "group": match.get("group"),
                "variables": dict(match.get("variables") or {}),
                "_merchant_names": [],
            }
        grouped[key]["_merchant_names"].append(result["merchant_name"])

    recipients = []
    for item in grouped.values():
        merchant_names = item.pop("_merchant_names")
        item["variables"]["merchant_names"] = "、".join(merchant_names)
        item["variables"]["merchant_count"] = len(merchant_names)
        if merchant_names:
            item["variables"]["merchant_name"] = merchant_names[0]
        recipients.append(item)
    return recipients


def first_value(row: Dict[str, str], field_names: Iterable[str]) -> str:
    for field_name in field_names:
        if row.get(field_name):
            return row[field_name]
    return ""


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_name(value: Any) -> str:
    return "".join(clean(value).lower().split())
