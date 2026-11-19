from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "database" / "schema.mysql.sql"
OPENAPI_PATH = ROOT / "openapi" / "openapi.yaml"
IA_PATH = ROOT / "docs" / "04-页面与后台信息架构.md"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_sql() -> int:
    sql = SQL_PATH.read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE TABLE\s+([a-z_]+)\s*\(", sql, re.I))
    required_tables = {
        "users",
        "staff_users",
        "stores",
        "video_categories",
        "videos",
        "video_watch_sessions",
        "video_watch_ticks",
        "user_video_progress",
        "point_accounts",
        "point_transactions",
        "gifts",
        "store_gift_inventory",
        "pickup_requests",
        "pickup_status_logs",
        "store_leads",
        "invitations",
        "system_configs",
        "admin_operation_logs",
        "idempotency_records",
    }
    require(required_tables <= tables, f"数据库缺少表: {sorted(required_tables - tables)}")

    required_fragments = {
        "观看片段去重": "UNIQUE KEY uk_watch_tick (user_id, video_id, tick_no)",
        "积分请求去重": "UNIQUE KEY uk_point_idempotency (idempotency_key)",
        "邀请人唯一绑定": "UNIQUE KEY uk_invitation_invitee (invitee_user_id)",
        "领取编号唯一": "UNIQUE KEY uk_pickup_request_no (request_no)",
        "领取凭证唯一": "UNIQUE KEY uk_pickup_voucher (voucher_code)",
        "门店礼品唯一库存": "UNIQUE KEY uk_store_gift (store_id, gift_id)",
        "用户邀请码唯一": "UNIQUE KEY uk_users_invite_code (invite_code)",
        "重要请求去重": "UNIQUE KEY uk_idempotency_actor_operation_key (actor_type, actor_id, operation, idempotency_key)",
    }
    for label, fragment in required_fragments.items():
        require(fragment in sql, f"缺少关键约束: {label}")

    forbidden_tables = {
        "orders",
        "shopping_carts",
        "shipping_addresses",
        "shipments",
        "logistics",
        "payments",
    }
    require(not (tables & forbidden_tables), f"发现不应存在的商城/物流表: {sorted(tables & forbidden_tables)}")
    return len(tables)


def iter_operations(spec: dict):
    http_methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() in http_methods:
                yield path, method.lower(), operation


def validate_local_refs(spec: dict) -> None:
    refs: list[str] = []

    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "$ref":
                    refs.append(child)
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(spec)
    for ref in refs:
        require(ref.startswith("#/"), f"不允许尚未验证的外部引用: {ref}")
        target = spec
        for part in ref[2:].split("/"):
            require(isinstance(target, dict) and part in target, f"OpenAPI 引用不存在: {ref}")
            target = target[part]


def validate_openapi() -> int:
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    require(spec.get("openapi") == "3.0.3", "OpenAPI 版本不正确")
    validate_local_refs(spec)
    paths = spec.get("paths", {})
    required_operations = {
        ("/auth/wechat-login", "post"),
        ("/videos", "get"),
        ("/watch-sessions", "post"),
        ("/watch-sessions/{sessionToken}/heartbeats", "post"),
        ("/watch-sessions/{sessionToken}/complete", "post"),
        ("/me/point-transactions", "get"),
        ("/gifts", "get"),
        ("/stores", "get"),
        ("/pickup-requests", "post"),
        ("/pickup-requests/{requestNo}", "get"),
        ("/pickup-requests/{requestNo}/cancel", "post"),
        ("/pickups/{pickupId}/voucher-qr", "post"),
        ("/me/invitation", "get"),
        ("/me/invitations", "get"),
        ("/me/invitation-mini-code", "post"),
        ("/staff/pickup-requests/{requestNo}/contacted", "post"),
        ("/staff/pickup-requests/{requestNo}/verify", "post"),
        ("/staff/pickup-requests/{requestNo}/reject", "post"),
        ("/admin/uploads/presign", "post"),
        ("/admin/storage/assets", "get"),
        ("/admin/storage/assets/cleanup-request", "post"),
        ("/admin/storage/assets/cleanup-cancel", "post"),
        ("/admin/storage/assets/delete", "post"),
    }
    actual_operations = {(path, method) for path, method, _ in iter_operations(spec)}
    require(required_operations <= actual_operations, f"核心接口缺失: {sorted(required_operations - actual_operations)}")

    operation_ids: list[str] = []
    for path, method, operation in iter_operations(spec):
        operation_id = operation.get("operationId")
        require(bool(operation_id), f"{method.upper()} {path} 缺少 operationId")
        operation_ids.append(operation_id)

        declared = set(re.findall(r"{([^}]+)}", path))
        parameters = operation.get("parameters", [])
        parameter_names: set[str] = set()
        for parameter in parameters:
            if "$ref" in parameter:
                ref_name = parameter["$ref"].split("/")[-1]
                ref_value = spec["components"]["parameters"][ref_name]
                if ref_value.get("in") == "path":
                    parameter_names.add(ref_value["name"])
            elif parameter.get("in") == "path":
                parameter_names.add(parameter["name"])
        require(declared == parameter_names, f"{method.upper()} {path} 路径参数不一致: {declared} != {parameter_names}")
        if method in {"post", "put", "patch"}:
            require("requestBody" in operation, f"{method.upper()} {path} 缺少请求体定义")

    require(len(operation_ids) == len(set(operation_ids)), "OpenAPI 中 operationId 重复")

    idempotent_operations = {
        ("/watch-sessions/{sessionToken}/heartbeats", "post"),
        ("/watch-sessions/{sessionToken}/complete", "post"),
        ("/pickup-requests", "post"),
        ("/pickup-requests/{requestNo}/cancel", "post"),
        ("/staff/pickup-requests/{requestNo}/verify", "post"),
    }
    for path, method in idempotent_operations:
        parameters = paths[path][method].get("parameters", [])
        refs = {item.get("$ref", "") for item in parameters}
        require("#/components/parameters/IdempotencyKey" in refs, f"{method.upper()} {path} 缺少幂等键")

    return len(actual_operations)


def validate_page_routes() -> int:
    text = IA_PATH.read_text(encoding="utf-8")
    mini_pages = set(re.findall(r"`(MP-\d{2})`", text))
    admin_pages = set(re.findall(r"`(AD-\d{2})`", text))
    require({f"MP-{index:02d}" for index in range(1, 15)} <= mini_pages, "小程序页面编号不完整")
    require({f"AD-{index:02d}" for index in range(1, 14)} <= admin_pages, "后台页面编号不完整")

    loop_markers = {
        "闭环A：观看积分",
        "闭环B：礼品引流到店",
        "闭环C：邀请好友",
    }
    for marker in loop_markers:
        require(marker in text, f"页面文档缺少 {marker}")

    forbidden_page_names = ["购物车", "收货地址", "物流", "线上支付"]
    for phrase in forbidden_page_names:
        listed_as_page = re.search(rf"\| `(?:MP|AD)-\d{{2}}` \| [^|\n]*{phrase}", text)
        require(listed_as_page is None, f"发现不应设计的页面: {phrase}")
    return len(mini_pages) + len(admin_pages)


def validate_loop_traceability() -> None:
    sql = SQL_PATH.read_text(encoding="utf-8")
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    operations = {(path, method) for path, method, _ in iter_operations(spec)}
    pages = IA_PATH.read_text(encoding="utf-8")

    loops = {
        "观看积分": {
            "tables": ["video_watch_sessions", "video_watch_ticks", "user_video_progress", "point_transactions"],
            "operations": [
                ("/watch-sessions", "post"),
                ("/watch-sessions/{sessionToken}/heartbeats", "post"),
                ("/me/point-transactions", "get"),
            ],
            "pages": ["MP-03", "MP-11"],
        },
        "到店领取": {
            "tables": ["gifts", "store_gift_inventory", "pickup_requests", "pickup_status_logs", "store_leads"],
            "operations": [
                ("/pickup-requests", "post"),
                ("/pickup-requests/{requestNo}", "get"),
                ("/staff/pickup-requests/{requestNo}/verify", "post"),
            ],
            "pages": ["MP-07", "MP-08", "AD-08", "AD-09"],
        },
        "邀请好友": {
            "tables": ["invitations", "point_transactions"],
            "operations": [
                ("/auth/wechat-login", "post"),
                ("/me/invitation", "get"),
                ("/me/invitations", "get"),
            ],
            "pages": ["MP-04", "MP-12"],
        },
    }

    for loop_name, requirements in loops.items():
        for table in requirements["tables"]:
            require(re.search(rf"CREATE TABLE\s+{table}\s*\(", sql, re.I) is not None, f"{loop_name} 缺少数据表 {table}")
        for operation in requirements["operations"]:
            require(operation in operations, f"{loop_name} 缺少接口 {operation}")
        for page in requirements["pages"]:
            require(f"`{page}`" in pages, f"{loop_name} 缺少页面 {page}")


def main() -> int:
    try:
        table_count = validate_sql()
        operation_count = validate_openapi()
        page_count = validate_page_routes()
        validate_loop_traceability()
    except (AssertionError, OSError, yaml.YAMLError) as exc:
        print(f"DESIGN_VALIDATION_FAILED: {exc}")
        return 1

    print(f"DATABASE_OK: {table_count} tables and key constraints")
    print(f"OPENAPI_OK: {operation_count} operations, unique ids and path parameters")
    print(f"PAGE_ARCHITECTURE_OK: {page_count} numbered mini-program/admin pages")
    print("LOOP_A_OK: watch -> valid time -> point ledger -> balance query")
    print("LOOP_B_OK: gift -> store pickup request -> lead -> verification/refund")
    print("LOOP_C_OK: invite binding -> qualification -> reward ledger -> result query")
    print("DESIGN_VALIDATION_PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
