import csv
import json
import time
from typing import Any, Dict, Iterable, List, Tuple
from urllib import parse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from miukoo_bot.config import Settings


class MerchantLookupError(ValueError):
    pass


LARK_TENANT_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
LARK_USER_SEARCH_URL = "https://open.feishu.cn/open-apis/contact/v3/users/search"
LARK_DEPARTMENT_URL = "https://open.feishu.cn/open-apis/contact/v3/departments/{}"

MERCHANT_NAME_FIELDS = (
    "merchant_name",
    "total_merchant_name",
    "head_merchant_name",
    "shop_name",
    "poi_name",
    "store_name",
    "总户商家名称",
    "总户商户名称",
    "总户名称",
    "商家名称",
    "商户名称",
    "门店名称",
    "POI名称",
)
BD_ID_FIELDS = (
    "bd_id",
    "bd_user_id",
    "sales_id",
    "sales_user_id",
    "BDID",
    "bd编号",
    "销售ID",
    "销售id",
    "销售工号",
)
BD_NAME_FIELDS = (
    "bd_name",
    "sales_name",
    "sales_name_latest",
    "name",
    "owner_name",
    "BD",
    "bd姓名",
    "BD姓名",
    "销售名称_最新",
    "销售名称",
    "销售",
    "销售负责人",
)
CONTACT_ID_FIELDS = (
    "contact_id",
    "open_id",
    "openId",
    "user_id",
    "userId",
    "mobile",
    "手机号",
    "飞书open_id",
    "飞书user_id",
    "飞书ID",
    "销售飞书open_id",
    "销售飞书user_id",
)
GROUP_FIELDS = ("group", "region", "city_group", "区域", "分组", "城市")
DEPARTMENT_FIELDS = (
    "department",
    "department_name",
    "department_path",
    "部门",
    "所属部门",
    "部门路径",
)


class MerchantBDLookup:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cached_access_token = ""
        self._cached_access_token_expires_at = 0.0
        self._cached_lark_access_token = ""
        self._cached_lark_access_token_expires_at = 0.0
        self._cached_sales_contact_directory: List[Dict[str, Any]] = []
        self._cached_lark_department_names: Dict[str, str] = {}

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

        self._resolve_sales_contacts(result)
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

    def _resolve_sales_contacts(self, response: Dict[str, Any]) -> None:
        resolved_by_name: Dict[str, List[Dict[str, Any]]] = {}
        for result in response["results"]:
            if not result.get("matches"):
                continue
            for match in result["matches"]:
                if match.get("contact_id"):
                    continue
                sales_name = match.get("sales_name") or match.get("name")
                if not sales_name:
                    continue
                if sales_name not in resolved_by_name:
                    resolved_by_name[sales_name] = self._lookup_sales_contacts(
                        sales_name
                    )
                resolution = resolve_sales_contact(
                    sales_name,
                    resolved_by_name[sales_name],
                    self.settings.sales_target_department,
                )
                match["sales_resolution"] = resolution
                if resolution["status"] in ("resolved", "resolved_by_department"):
                    selected = resolution["selected"]
                    match["contact_id"] = selected.get("contact_id") or ""
                    match["bd_id"] = match.get("bd_id") or selected.get("bd_id") or sales_name
                    match["group"] = match.get("group") or selected.get("department") or ""
                    match["department"] = selected.get("department") or ""
                    match["variables"]["department"] = selected.get("department") or ""
                elif result["status"] == "matched":
                    result["status"] = "ambiguous"
                    result["message"] = resolution["message"]

    def _lookup_sales_contacts(self, sales_name: str) -> List[Dict[str, Any]]:
        provider = (self.settings.sales_contact_lookup_provider or "auto").lower()
        if provider not in ("auto", "csv", "lark", "feishu"):
            raise MerchantLookupError(
                "Unsupported sales contact lookup provider: {}".format(provider)
            )

        has_lark_credentials = bool(
            self.settings.lark_app_id and self.settings.lark_app_secret
        )
        if provider in ("lark", "feishu") or (
            provider == "auto" and has_lark_credentials
        ):
            return self._lookup_sales_contacts_from_lark(sales_name)
        return [
            item
            for item in self._load_sales_contact_directory()
            if normalize_name(item.get("name")) == normalize_name(sales_name)
        ]

    def _load_sales_contact_directory(self) -> List[Dict[str, Any]]:
        if self._cached_sales_contact_directory:
            return self._cached_sales_contact_directory

        path = self.settings.sales_contact_directory_csv
        if not path:
            return []
        try:
            with open(path, newline="", encoding="utf-8-sig") as csv_file:
                self._cached_sales_contact_directory = [
                    normalize_sales_contact(row) for row in csv.DictReader(csv_file)
                ]
                return self._cached_sales_contact_directory
        except FileNotFoundError:
            return []

    def _lookup_sales_contacts_from_lark(self, sales_name: str) -> List[Dict[str, Any]]:
        token = self._resolve_lark_access_token()
        query = parse.urlencode(
            {
                "user_id_type": self.settings.lark_receive_id_type,
                "department_id_type": self.settings.lark_department_id_type,
            }
        )
        decoded = self._lark_post_json(
            "{}?{}".format(LARK_USER_SEARCH_URL, query),
            {"query": sales_name, "page_size": 20},
            token,
        )
        items = extract_lark_user_items(decoded)
        contacts = [
            normalize_lark_user_contact(item, self.settings.lark_receive_id_type)
            for item in items
            if item
        ]
        self._hydrate_lark_department_names(contacts, token)
        return [
            contact
            for contact in contacts
            if normalize_name(contact.get("name")) == normalize_name(sales_name)
        ]

    def _resolve_lark_access_token(self) -> str:
        if not self.settings.lark_app_id or not self.settings.lark_app_secret:
            raise MerchantLookupError(
                "LARK_APP_ID and LARK_APP_SECRET are required for Feishu user lookup"
            )

        now = time.time()
        if (
            self._cached_lark_access_token
            and self._cached_lark_access_token_expires_at
            and now < self._cached_lark_access_token_expires_at - 60
        ):
            return self._cached_lark_access_token

        request = Request(
            LARK_TENANT_TOKEN_URL,
            data=json.dumps(
                {
                    "app_id": self.settings.lark_app_id,
                    "app_secret": self.settings.lark_app_secret,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.lark_timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise MerchantLookupError(
                "Feishu token request failed: HTTP {}".format(exc.code)
            ) from exc
        except URLError as exc:
            raise MerchantLookupError(
                "Feishu token request failed: {}".format(exc)
            ) from exc
        except json.JSONDecodeError as exc:
            raise MerchantLookupError(
                "Feishu token response returned invalid JSON"
            ) from exc

        code = decoded.get("code")
        if code not in (0, None):
            raise MerchantLookupError(
                "Feishu token request failed: {}".format(
                    decoded.get("msg") or decoded
                )
            )

        token = clean(decoded.get("tenant_access_token"))
        if not token:
            raise MerchantLookupError("Feishu token response missing tenant_access_token")

        expires_in = decoded.get("expire") or decoded.get("expires_in") or 7200
        try:
            parsed_expires_in = int(expires_in)
        except (TypeError, ValueError):
            parsed_expires_in = 7200
        self._cached_lark_access_token = token
        self._cached_lark_access_token_expires_at = now + parsed_expires_in
        return token

    def _lark_post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        token: str,
    ) -> Dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer {}".format(token),
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        return self._send_lark_request(request)

    def _lark_get_json(self, url: str, token: str) -> Dict[str, Any]:
        request = Request(
            url,
            headers={
                "Authorization": "Bearer {}".format(token),
                "Content-Type": "application/json; charset=utf-8",
            },
            method="GET",
        )
        return self._send_lark_request(request)

    def _send_lark_request(self, request: Request) -> Dict[str, Any]:
        try:
            with urlopen(request, timeout=self.settings.lark_timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise MerchantLookupError(
                "Feishu contact lookup failed: HTTP {}".format(exc.code)
            ) from exc
        except URLError as exc:
            raise MerchantLookupError(
                "Feishu contact lookup failed: {}".format(exc)
            ) from exc
        except json.JSONDecodeError as exc:
            raise MerchantLookupError(
                "Feishu contact lookup returned invalid JSON"
            ) from exc

        code = decoded.get("code")
        if code not in (0, None):
            raise MerchantLookupError(
                "Feishu contact lookup failed: {}".format(
                    decoded.get("msg") or decoded
                )
            )
        return decoded

    def _hydrate_lark_department_names(
        self,
        contacts: List[Dict[str, Any]],
        token: str,
    ) -> None:
        for contact in contacts:
            if contact.get("department"):
                continue
            department_ids = contact.get("_department_ids") or []
            names = []
            for department_id in department_ids:
                department_id = clean(department_id)
                if not department_id:
                    continue
                if department_id not in self._cached_lark_department_names:
                    url = "{}?{}".format(
                        LARK_DEPARTMENT_URL.format(parse.quote(department_id, safe="")),
                        parse.urlencode(
                            {
                                "department_id_type": (
                                    self.settings.lark_department_id_type
                                )
                            }
                        ),
                    )
                    decoded = self._lark_get_json(url, token)
                    self._cached_lark_department_names[department_id] = (
                        extract_lark_department_name(decoded)
                    )
                name = self._cached_lark_department_names.get(department_id)
                if name:
                    names.append(name)
            contact["department"] = " / ".join(names)


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
    name = first_value(cleaned, BD_NAME_FIELDS)
    bd_id = first_value(cleaned, BD_ID_FIELDS) or name
    contact_id = first_value(cleaned, CONTACT_ID_FIELDS)
    group = first_value(cleaned, GROUP_FIELDS)
    department = first_value(cleaned, DEPARTMENT_FIELDS)
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
            + DEPARTMENT_FIELDS
        )
    }
    variables["merchant_name"] = merchant_name
    variables["sales_name"] = name
    if department:
        variables["department"] = department
    return {
        "merchant_name": merchant_name,
        "bd_id": bd_id,
        "sales_name": name,
        "name": name,
        "contact_id": contact_id,
        "group": group,
        "department": department,
        "variables": variables,
    }


def normalize_sales_contact(row: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {str(key).strip(): clean(value) for key, value in row.items()}
    name = first_value(cleaned, BD_NAME_FIELDS)
    contact_id = first_value(cleaned, CONTACT_ID_FIELDS)
    department = first_value(cleaned, DEPARTMENT_FIELDS)
    bd_id = first_value(cleaned, BD_ID_FIELDS) or name
    return {
        "name": name,
        "bd_id": bd_id,
        "contact_id": contact_id,
        "department": department,
    }


def normalize_lark_user_contact(
    item: Dict[str, Any],
    receive_id_type: str,
) -> Dict[str, Any]:
    name = clean(
        item.get("name")
        or item.get("cn_name")
        or item.get("nickname")
        or item.get("en_name")
    )
    open_id = clean(item.get("open_id") or item.get("openId"))
    user_id = clean(item.get("user_id") or item.get("userId"))
    if receive_id_type == "open_id":
        contact_id = open_id or user_id
    elif receive_id_type == "user_id":
        contact_id = user_id or open_id
    else:
        contact_id = user_id or open_id

    department_text, department_ids = extract_lark_department_data(item)
    return {
        "name": name,
        "bd_id": user_id or open_id or name,
        "contact_id": contact_id,
        "department": department_text,
        "_department_ids": department_ids,
    }


def extract_lark_user_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    candidates = (
        data.get("items")
        or data.get("users")
        or data.get("user_list")
        or data.get("result")
        or []
    )
    if isinstance(candidates, dict):
        candidates = candidates.get("items") or candidates.get("users") or []
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def extract_lark_department_data(item: Dict[str, Any]) -> Tuple[str, List[str]]:
    names: List[str] = []
    department_ids: List[str] = []

    for key in DEPARTMENT_FIELDS + (
        "departments",
        "department_id",
        "department_ids",
        "open_department_id",
        "open_department_ids",
        "department_names",
        "department_path",
    ):
        value = item.get(key)
        if not value:
            continue
        if key in (
            "department_id",
            "department_ids",
            "open_department_id",
            "open_department_ids",
        ):
            department_ids.extend(flatten_string_values(value))
            continue
        extracted_names, extracted_ids = normalize_department_value(value)
        names.extend(extracted_names)
        department_ids.extend(extracted_ids)

    return " / ".join(dedupe_non_empty(names)), dedupe_non_empty(department_ids)


def normalize_department_value(value: Any) -> Tuple[List[str], List[str]]:
    if isinstance(value, str):
        return [value], []
    if isinstance(value, dict):
        i18n_name = value.get("i18n_name")
        if not isinstance(i18n_name, dict):
            i18n_name = {}
        name = clean(
            value.get("name")
            or value.get("department_name")
            or value.get("department_path")
            or i18n_name.get("zh_cn")
            or i18n_name.get("zh-CN")
        )
        department_id = clean(
            value.get("department_id")
            or value.get("open_department_id")
            or value.get("id")
        )
        return ([name] if name else []), ([department_id] if department_id else [])
    if isinstance(value, list):
        names: List[str] = []
        department_ids: List[str] = []
        for item in value:
            extracted_names, extracted_ids = normalize_department_value(item)
            names.extend(extracted_names)
            department_ids.extend(extracted_ids)
        return names, department_ids
    return [], []


def extract_lark_department_name(payload: Dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    department = data.get("department") if isinstance(data.get("department"), dict) else data
    if not isinstance(department, dict):
        return ""
    i18n_name = department.get("i18n_name")
    if not isinstance(i18n_name, dict):
        i18n_name = {}
    return clean(
        department.get("name")
        or department.get("department_name")
        or i18n_name.get("zh_cn")
        or i18n_name.get("zh-CN")
    )


def resolve_sales_contact(
    sales_name: str,
    directory: List[Dict[str, Any]],
    target_department: str,
) -> Dict[str, Any]:
    candidates = [
        item
        for item in directory
        if normalize_name(item.get("name")) == normalize_name(sales_name)
    ]
    visible_candidates = [
        {
            "name": item.get("name", ""),
            "bd_id": item.get("bd_id", ""),
            "contact_id": item.get("contact_id", ""),
            "department": item.get("department", ""),
        }
        for item in candidates
    ]
    if not candidates:
        return {
            "status": "not_found",
            "duplicate_name": False,
            "selected": {},
            "candidates": [],
            "message": "No Feishu contact found for sales name {}".format(sales_name),
        }

    if len(candidates) == 1:
        if not candidates[0].get("contact_id"):
            return {
                "status": "missing_contact_id",
                "duplicate_name": False,
                "selected": {},
                "candidates": visible_candidates,
                "message": "Feishu contact for {} has no sendable ID".format(
                    sales_name
                ),
            }
        return {
            "status": "resolved",
            "duplicate_name": False,
            "selected": candidates[0],
            "candidates": visible_candidates,
            "message": "",
        }

    target = clean(target_department)
    if target:
        target_matches = [
            item
            for item in candidates
            if target in clean(item.get("department"))
        ]
        if len(target_matches) == 1:
            if not target_matches[0].get("contact_id"):
                return {
                    "status": "missing_contact_id",
                    "duplicate_name": True,
                    "selected": {},
                    "candidates": visible_candidates,
                    "message": "Selected Feishu contact for {} has no sendable ID".format(
                        sales_name
                    ),
                }
            return {
                "status": "resolved_by_department",
                "duplicate_name": True,
                "selected": target_matches[0],
                "candidates": visible_candidates,
                "message": (
                    "Duplicate sales name {}; selected department {}".format(
                        sales_name,
                        target,
                    )
                ),
            }

    return {
        "status": "ambiguous",
        "duplicate_name": True,
        "selected": {},
        "candidates": visible_candidates,
        "message": "Duplicate sales name {} needs manual confirmation".format(
            sales_name
        ),
    }


def dedupe_non_empty(items: Iterable[str]) -> List[str]:
    deduped = []
    seen = set()
    for item in items:
        value = clean(item)
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def flatten_string_values(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        items: List[str] = []
        for item in value:
            items.extend(flatten_string_values(item))
        return items
    if isinstance(value, dict):
        return [
            clean(
                value.get("department_id")
                or value.get("open_department_id")
                or value.get("id")
            )
        ]
    return []


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
        grouped[key]["_merchant_names"].append(
            match.get("merchant_name") or result["merchant_name"]
        )

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
