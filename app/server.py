from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import qrcode

try:
    from .storage import delete_object as delete_storage_object
    from .storage import enabled as oss_enabled
    from .storage import list_media_objects
    from .storage import new_upload as new_oss_upload
    from .storage import resolve_url as resolve_storage_url
except ImportError:
    from storage import delete_object as delete_storage_object
    from storage import enabled as oss_enabled
    from storage import list_media_objects
    from storage import new_upload as new_oss_upload
    from storage import resolve_url as resolve_storage_url


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
ADMIN_ROOT = ROOT / "admin"
DB_PATH = ROOT / "database" / "dev.sqlite3"
SCHEMA_PATH = ROOT / "app" / "schema.sqlite.sql"
PASSWORD_ROUNDS = 210_000
SESSION_HOURS = 12
DEV_ENDPOINTS_ENABLED = os.environ.get("ENABLE_DEV_ENDPOINTS", "0") == "1"
MAX_JSON_BYTES = 1_048_576
MAX_IMAGE_BYTES = 3 * 1_048_576
MAX_VIDEO_BYTES = 50 * 1_048_576
UPLOAD_ROOT = WEB_ROOT / "uploads"
MEDIA_CLEANUP_GRACE_DAYS = 7


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def calculate_video_points(duration_seconds: int) -> int:
    """One point per minute, rounded half up to an integer."""
    return max(0, (int(duration_seconds) + 30) // 60)


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def database():
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(reset: bool = False) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if reset and DB_PATH.exists():
        with database() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            table_names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table_name in table_names:
                connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            connection.execute("PRAGMA foreign_keys = ON")
    with database() as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        if "wechat_openid" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN wechat_openid TEXT")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS uk_users_wechat_openid ON users(wechat_openid)")
        for column, definition in {
            "mobile": "TEXT NOT NULL DEFAULT ''",
            "tags": "TEXT NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
        }.items():
            if column not in user_columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")
        progress_columns = {row[1] for row in connection.execute("PRAGMA table_info(watch_progress)")}
        if "last_watched_at" not in progress_columns:
            connection.execute("ALTER TABLE watch_progress ADD COLUMN last_watched_at TEXT")
        if "rewarded_points" not in progress_columns:
            connection.execute("ALTER TABLE watch_progress ADD COLUMN rewarded_points INTEGER NOT NULL DEFAULT 0")
        session_columns = {row[1] for row in connection.execute("PRAGMA table_info(watch_sessions)")}
        if "counted_view" not in session_columns:
            connection.execute("ALTER TABLE watch_sessions ADD COLUMN counted_view INTEGER NOT NULL DEFAULT 0")
        video_columns = {row[1] for row in connection.execute("PRAGMA table_info(videos)")}
        if "cover_url" not in video_columns:
            connection.execute(
                "ALTER TABLE videos ADD COLUMN cover_url TEXT NOT NULL DEFAULT '/assets/video-conference.svg'"
            )
        if "total_points" not in video_columns:
            connection.execute("ALTER TABLE videos ADD COLUMN total_points INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                "UPDATE videos SET total_points=CAST((duration_seconds + 30) / 60 AS INTEGER)"
            )
        if "tags" not in video_columns:
            connection.execute("ALTER TABLE videos ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
        if "rewarded_points" not in progress_columns:
            connection.execute(
                """UPDATE watch_progress SET rewarded_points=MIN(
                       (SELECT total_points FROM videos WHERE videos.id=watch_progress.video_id),
                       COALESCE((SELECT SUM(amount) FROM point_transactions
                                 WHERE point_transactions.user_id=watch_progress.user_id
                                   AND transaction_type='VIDEO_REWARD'
                                   AND business_key LIKE 'watch:' || watch_progress.user_id || ':' || watch_progress.video_id || ':%'), 0)
                   )"""
            )
        invitation_columns = {row[1] for row in connection.execute("PRAGMA table_info(invitations)")}
        for column, definition in {
            "source_type": "TEXT NOT NULL DEFAULT 'SHARE_LINK'",
            "source_value": "TEXT NOT NULL DEFAULT ''",
            "landing_scene": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in invitation_columns:
                connection.execute(f"ALTER TABLE invitations ADD COLUMN {column} {definition}")
        gift_columns = {row[1] for row in connection.execute("PRAGMA table_info(gifts)")}
        if "image_url" not in gift_columns:
            connection.execute("ALTER TABLE gifts ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
        if "image_urls" not in gift_columns:
            connection.execute("ALTER TABLE gifts ADD COLUMN image_urls TEXT NOT NULL DEFAULT '[]'")
        gift_images = {
            "soap": "/assets/gift-soap.svg",
            "water": "/assets/gift-water.svg",
            "mist": "/assets/gift-mist.svg",
            "cup": "/assets/gift-cup.svg",
        }
        for image_key, image_url in gift_images.items():
            connection.execute(
                "UPDATE gifts SET image_url=? WHERE image_key=? AND image_url=''",
                (image_url, image_key),
            )
        connection.execute(
            "UPDATE gifts SET image_urls=json_array(image_url) WHERE image_urls='[]' AND image_url<>''"
        )
        if DEV_ENDPOINTS_ENABLED and connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            seed_database(connection)
        ensure_runtime_seed(connection)


def seed_database(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO users (name, invite_code, points) VALUES (?, ?, ?)",
        ("自动化测试用户", "WANG2026", 5),
    )
    connection.execute(
        """INSERT INTO point_transactions
           (user_id, amount, balance_after, transaction_type, business_key, description)
           VALUES (1, 5, 5, 'MANUAL_ADJUST', 'seed:initial-points', '自动化测试初始积分')"""
    )
    video_rows = [
        ("自动化测试视频一", "仅供自动化测试。", "/assets/video-conference.svg", "/media/knowledge-demo.mp4", "自动化测试", 45, 1),
        ("自动化测试视频二", "仅供自动化测试。", "/assets/video-interview.svg", "/media/knowledge-demo.mp4", "自动化测试", 45, 1),
        ("自动化测试视频三", "仅供自动化测试。", "/assets/video-conference.svg", "/media/knowledge-demo.mp4", "自动化测试", 45, 1),
        ("自动化测试视频四", "仅供自动化测试。", "/assets/video-interview.svg", "/media/knowledge-demo.mp4", "自动化测试", 45, 1),
    ]
    connection.executemany(
        """INSERT INTO videos
           (title, summary, cover_url, media_url, category, duration_seconds, total_points)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        video_rows,
    )
    connection.execute(
        "INSERT INTO stores (name, address, contact_phone, business_hours) VALUES (?, ?, ?, ?)",
        ("自动化测试门店", "测试地址", "400-000-0000", "09:00–18:00"),
    )
    gift_rows = [
        ("自动化测试礼品一", "仅供自动化测试。", 68, "soap", "/assets/gift-soap.svg", 3),
        ("自动化测试礼品二", "仅供自动化测试。", 288, "water", "/assets/gift-water.svg", 8),
        ("自动化测试礼品三", "仅供自动化测试。", 699, "mist", "/assets/gift-mist.svg", 15),
        ("自动化测试礼品四", "仅供自动化测试。", 899, "cup", "/assets/gift-cup.svg", 20),
    ]
    connection.executemany(
        """INSERT INTO gifts
           (name, description, market_price, image_key, image_url, points_cost)
           VALUES (?, ?, ?, ?, ?, ?)""",
        gift_rows,
    )
    connection.executemany(
        """INSERT INTO store_gift_inventory
           (store_id, gift_id, available_quantity, locked_quantity, redeemed_quantity)
           VALUES (1, ?, ?, 0, 0)""",
        [(1, 8), (2, 18), (3, 6), (4, 9)],
    )


def password_hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PASSWORD_ROUNDS
    ).hex()


def ensure_runtime_seed(connection: sqlite3.Connection) -> None:
    """Create local-only operator accounts and editable configuration defaults."""
    if connection.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0] == 0:
        default_password = os.environ.get("LOCAL_ADMIN_PASSWORD", "").strip()
        if not default_password and DEV_ENDPOINTS_ENABLED:
            default_password = "dev-only-password"
        if not default_password:
            raise RuntimeError("正式环境首次启动前必须设置 LOCAL_ADMIN_PASSWORD")
        salt = secrets.token_hex(16)
        connection.execute(
            """INSERT INTO staff_users
               (username, password_salt, password_hash, display_name, role)
               VALUES (?, ?, ?, ?, 'ADMIN')""",
            ("admin", salt, password_hash(default_password, salt), "系统管理员"),
        )
    seed_default_verifier = DEV_ENDPOINTS_ENABLED or os.environ.get("SEED_DEFAULT_VERIFIER", "0") == "1"
    if (
        seed_default_verifier
        and connection.execute("SELECT 1 FROM stores WHERE id=1").fetchone()
        and not connection.execute("SELECT 1 FROM staff_users WHERE username='verifier'").fetchone()
    ):
        verifier_password = os.environ.get("LOCAL_VERIFIER_PASSWORD", "Verifier123!")
        salt = secrets.token_hex(16)
        connection.execute(
            """INSERT INTO staff_users
               (username, password_salt, password_hash, display_name, role, store_id)
               VALUES (?, ?, ?, ?, 'STORE_VERIFIER', 1)""",
            ("verifier", salt, password_hash(verifier_password, salt), "门店核销员"),
        )
    defaults = [
        ("pickup_valid_days", "7", "到店领取凭证有效天数"),
        ("invite_inviter_points", "50", "邀请人奖励积分"),
        ("invite_invitee_points", "10", "受邀新用户奖励积分"),
        ("customer_service_phone", "", "小程序客服电话"),
        ("admin_description_dashboard", "聚合展示内容、用户、到店申请和转化情况。", "运营概览页说明"),
        ("admin_description_videos", "新增、上传、编辑和上下架；发布后小程序刷新即可看到。", "知识视频页说明"),
        ("admin_description_watch", "查看每位会员对各视频的有效观看时长和实际获得积分。", "观看数据页说明"),
        ("admin_description_members", "集中查看资料、累计观看、积分、邀请上下级和来源。", "会员管理页说明"),
        ("admin_description_gifts", "维护礼品、积分价格和库存，并处理用户到店领取。", "礼品与领取页说明"),
        ("admin_description_invites", "明确记录谁邀请了谁、入口来源和奖励状态。", "邀请管理页说明"),
        ("admin_description_system", "维护门店、公司人员账号、文件与操作日志。", "配置与日志页说明"),
    ]
    connection.executemany(
        """INSERT OR IGNORE INTO system_configs (config_key, config_value, description)
           VALUES (?, ?, ?)""",
        defaults,
    )
    connection.execute(
        "DELETE FROM system_configs WHERE config_key IN ('watch_reward_interval_seconds','watch_reward_points')"
    )


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def config_int(connection: sqlite3.Connection, key: str, default: int) -> int:
    row = connection.execute("SELECT config_value FROM system_configs WHERE config_key=?", (key,)).fetchone()
    return int(row[0]) if row else default


def config_text(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT config_value FROM system_configs WHERE config_key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def parse_gift_image_refs(raw: object, fallback: str = "") -> list[str]:
    values: list[object]
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            values = parsed if isinstance(parsed, list) else [raw]
        except json.JSONDecodeError:
            values = [part.strip() for part in raw.split(",")]
    else:
        values = []
    refs: list[str] = []
    for value in values:
        ref = str(value or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    if not refs and fallback:
        refs.append(str(fallback).strip())
    return refs[:8]


def prepare_gift_images(gift: dict, include_storage_refs: bool = False) -> None:
    refs = parse_gift_image_refs(gift.get("image_urls"), str(gift.get("image_url", "")))
    urls = [resolve_storage_url(ref) for ref in refs]
    gift["image_url"] = urls[0] if urls else ""
    gift["image_urls"] = urls
    if include_storage_refs:
        gift["image_storage_ref"] = refs[0] if refs else ""
        gift["image_storage_refs"] = refs


def row_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def add_points(
    connection: sqlite3.Connection,
    user_id: int,
    amount: int,
    transaction_type: str,
    business_key: str,
    description: str,
) -> int:
    existing = connection.execute(
        "SELECT balance_after FROM point_transactions WHERE business_key = ?", (business_key,)
    ).fetchone()
    if existing:
        return int(existing["balance_after"])

    user = connection.execute("SELECT points FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise ApiError("USER_NOT_FOUND", "用户不存在", HTTPStatus.NOT_FOUND)
    new_balance = int(user["points"]) + amount
    if new_balance < 0:
        raise ApiError("INSUFFICIENT_POINTS", "积分不足，无法完成本次申请", HTTPStatus.CONFLICT)
    connection.execute("UPDATE users SET points = ? WHERE id = ?", (new_balance, user_id))
    connection.execute(
        """INSERT INTO point_transactions
           (user_id, amount, balance_after, transaction_type, business_key, description)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, amount, new_balance, transaction_type, business_key, description),
    )
    return new_balance


def replay_idempotent(
    connection: sqlite3.Connection, actor_type: str, actor_id: int, operation: str, request_key: str
) -> dict | None:
    row = connection.execute(
        """SELECT response_json FROM idempotency_records
           WHERE actor_type = ? AND actor_id = ? AND operation = ? AND request_key = ?""",
        (actor_type, actor_id, operation, request_key),
    ).fetchone()
    return json.loads(row["response_json"]) if row else None


def save_idempotent(
    connection: sqlite3.Connection,
    actor_type: str,
    actor_id: int,
    operation: str,
    request_key: str,
    response: dict,
) -> None:
    connection.execute(
        """INSERT INTO idempotency_records
           (actor_type, actor_id, operation, request_key, response_json)
           VALUES (?, ?, ?, ?, ?)""",
        (actor_type, actor_id, operation, request_key, json.dumps(response, ensure_ascii=False)),
    )


def maybe_reward_invitation(connection: sqlite3.Connection, invitee_id: int) -> dict | None:
    invitation = connection.execute(
        "SELECT * FROM invitations WHERE invitee_user_id = ? AND status = 'BOUND'", (invitee_id,)
    ).fetchone()
    if not invitation:
        return None

    invite_id = int(invitation["id"])
    inviter_id = int(invitation["inviter_user_id"])
    inviter_balance = add_points(
        connection,
        inviter_id,
        config_int(connection, "invite_inviter_points", 50),
        "INVITE_REWARD",
        f"invite:{invite_id}:inviter",
        "好友完成首次有效观看，邀请奖励",
    )
    invitee_balance = add_points(
        connection,
        invitee_id,
        config_int(connection, "invite_invitee_points", 10),
        "INVITE_REWARD",
        f"invite:{invite_id}:invitee",
        "完成首次有效观看，新用户奖励",
    )
    connection.execute(
        "UPDATE invitations SET status = 'REWARDED', rewarded_at = ? WHERE id = ?",
        (utc_now(), invite_id),
    )
    return {"inviterBalance": inviter_balance, "inviteeBalance": invitee_balance}


def bind_invitation_by_code(
    connection: sqlite3.Connection,
    invitee_id: int,
    invite_code: str,
    source_type: str = "SHARE_LINK",
    source_value: str = "",
    landing_scene: str = "",
) -> bool:
    normalized_code = invite_code.strip()
    if not normalized_code:
        return False
    inviter = connection.execute(
        "SELECT id FROM users WHERE invite_code=? AND status='ACTIVE'", (normalized_code,)
    ).fetchone()
    if not inviter or int(inviter["id"]) == invitee_id:
        return False
    if connection.execute("SELECT 1 FROM invitations WHERE invitee_user_id=?", (invitee_id,)).fetchone():
        return False
    inviter_id = int(inviter["id"])
    cycle = connection.execute(
        """WITH RECURSIVE descendants(user_id) AS (
               SELECT invitee_user_id FROM invitations WHERE inviter_user_id=?
               UNION
               SELECT i.invitee_user_id FROM invitations i
               JOIN descendants d ON i.inviter_user_id=d.user_id
           )
           SELECT 1 FROM descendants WHERE user_id=? LIMIT 1""",
        (invitee_id, inviter_id),
    ).fetchone()
    if cycle:
        return False
    connection.execute(
        """INSERT INTO invitations
           (inviter_user_id, invitee_user_id, source_type, source_value, landing_scene)
           VALUES (?, ?, ?, ?, ?)""",
        (
            inviter_id,
            invitee_id,
            (source_type.strip().upper() or "SHARE_LINK")[:30],
            source_value.strip()[:255],
            landing_scene.strip()[:100],
        ),
    )
    return True


def apply_watch_tick(connection: sqlite3.Connection, user_id: int, video_id: int, tick_no: int) -> dict:
    video = connection.execute(
        "SELECT * FROM videos WHERE id = ? AND status = 'PUBLISHED'", (video_id,)
    ).fetchone()
    if not video:
        raise ApiError("VIDEO_UNAVAILABLE", "视频不存在或未上架", HTTPStatus.NOT_FOUND)
    if tick_no <= 0 or tick_no * 5 > int(video["duration_seconds"]) + 5:
        raise ApiError("WATCH_HEARTBEAT_REJECTED", "播放位置不在有效范围", HTTPStatus.UNPROCESSABLE_ENTITY)

    cursor = connection.execute(
        "INSERT OR IGNORE INTO watch_ticks (user_id, video_id, tick_no) VALUES (?, ?, ?)",
        (user_id, video_id, tick_no),
    )
    accepted_seconds = 5 if cursor.rowcount == 1 else 0
    awarded_points = 0
    invitation_reward = None

    connection.execute(
        """INSERT OR IGNORE INTO watch_progress
           (user_id, video_id, valid_seconds, rewarded_points) VALUES (?, ?, 0, 0)""",
        (user_id, video_id),
    )
    if accepted_seconds:
        connection.execute(
            """UPDATE watch_progress
               SET valid_seconds = MIN(valid_seconds + 5, ?), last_watched_at = ?
               WHERE user_id = ? AND video_id = ?""",
            (int(video["duration_seconds"]), utc_now(), user_id, video_id),
        )

    progress = connection.execute(
        "SELECT * FROM watch_progress WHERE user_id = ? AND video_id = ?", (user_id, video_id)
    ).fetchone()
    valid_seconds = int(progress["valid_seconds"])
    completed = valid_seconds >= int(video["duration_seconds"])
    target_points = min(int(video["total_points"]), valid_seconds // 60)
    if completed:
        target_points = int(video["total_points"])
    awarded_points = max(0, target_points - int(progress["rewarded_points"]))
    if awarded_points:
        add_points(
            connection,
            user_id,
            awarded_points,
            "VIDEO_REWARD",
            f"watch:{user_id}:{video_id}:point:{target_points}",
            f"观看《{video['title']}》累计{valid_seconds // 60}分钟（积分{target_points}/{video['total_points']}）",
        )
        connection.execute(
            "UPDATE watch_progress SET rewarded_points = ? WHERE user_id = ? AND video_id = ?",
            (target_points, user_id, video_id),
        )
        invitation_reward = maybe_reward_invitation(connection, user_id)

    balance = connection.execute("SELECT points FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    return {
        "acceptedSeconds": accepted_seconds,
        "validSeconds": valid_seconds,
        "completed": completed,
        "awardedPoints": awarded_points,
        "balance": int(balance),
        "invitationReward": invitation_reward,
    }


def snapshot(user_id: int) -> dict:
    with database() as connection:
        user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise ApiError("USER_NOT_FOUND", "用户不存在", HTTPStatus.NOT_FOUND)
        if user["status"] == "DISABLED":
            raise ApiError("USER_DISABLED", "会员账号已停用", HTTPStatus.FORBIDDEN)
        video = connection.execute("SELECT * FROM videos WHERE id = 1").fetchone()
        videos = [
            dict(row)
            for row in connection.execute(
                """SELECT v.*,
                          COALESCE(p.valid_seconds, 0) AS user_valid_seconds,
                          COALESCE(p.rewarded_points, 0) AS user_rewarded_points,
                          COALESCE(views.view_count, 0) AS view_count
                   FROM videos v
                   LEFT JOIN watch_progress p ON p.video_id=v.id AND p.user_id=?
                   LEFT JOIN (
                     SELECT video_id, COUNT(*) AS view_count
                     FROM watch_sessions WHERE counted_view=1 GROUP BY video_id
                   ) views ON views.video_id=v.id
                   WHERE v.status='PUBLISHED' ORDER BY v.id""",
                (user_id,),
            )
        ]
        progress = connection.execute(
            "SELECT * FROM watch_progress WHERE user_id = ? AND video_id = 1", (user_id,)
        ).fetchone()
        gift = connection.execute("SELECT * FROM gifts WHERE status='ACTIVE' ORDER BY id LIMIT 1").fetchone()
        gifts = [
            dict(row)
            for row in connection.execute(
                """SELECT g.*, i.store_id, s.name AS store_name, i.available_quantity,
                          i.locked_quantity, i.redeemed_quantity
                   FROM gifts g
                   JOIN store_gift_inventory i ON i.gift_id = g.id
                   JOIN stores s ON s.id=i.store_id
                   WHERE g.status = 'ACTIVE' AND s.status='ACTIVE' ORDER BY g.id, i.store_id"""
            )
        ]
        stores = [dict(row) for row in connection.execute("SELECT * FROM stores WHERE status='ACTIVE' ORDER BY id")]
        store = stores[0] if stores else None
        inventory = connection.execute("SELECT * FROM store_gift_inventory ORDER BY store_id,gift_id LIMIT 1").fetchone()
        transactions = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM point_transactions WHERE user_id = ? ORDER BY id DESC", (user_id,)
            )
        ]
        pickups = [
            dict(row)
            for row in connection.execute(
                """SELECT p.*, g.name AS gift_name, s.name AS store_name
                   FROM pickup_requests p
                   JOIN gifts g ON g.id = p.gift_id
                   JOIN stores s ON s.id = p.store_id
                   WHERE p.user_id = ? ORDER BY p.id DESC""",
                (user_id,),
            )
        ]
        invitations = [
            dict(row)
            for row in connection.execute(
                """SELECT i.id, i.invitee_user_id, i.status, i.created_at, i.rewarded_at,
                          b.name AS invitee_name
                   FROM invitations i JOIN users b ON b.id=i.invitee_user_id
                   WHERE i.inviter_user_id=? ORDER BY i.id DESC""",
                (user_id,),
            )
        ]
        for item in videos:
            item["cover_url"] = resolve_storage_url(item["cover_url"])
            item["media_url"] = resolve_storage_url(item["media_url"])
        for item in gifts:
            prepare_gift_images(item)
        video_item = dict(video) if video else None
        gift_item = dict(gift) if gift else None
        if video_item:
            video_item["cover_url"] = resolve_storage_url(video_item["cover_url"])
            video_item["media_url"] = resolve_storage_url(video_item["media_url"])
        if gift_item:
            prepare_gift_images(gift_item)
        return {
            "user": dict(user),
            "video": video_item,
            "videos": videos,
            "progress": dict(progress) if progress else {"valid_seconds": 0, "rewarded_points": 0},
            "transactions": transactions,
            "gift": gift_item,
            "gifts": gifts,
            "store": dict(store) if store else None,
            "stores": stores,
            "inventory": dict(inventory) if inventory else None,
            "pickups": pickups,
            "invitations": invitations,
            "settings": {"customerServicePhone": config_text(connection, "customer_service_phone")},
        }


def media_reference_set(connection: sqlite3.Connection) -> set[str]:
    references: set[str] = set()
    for table, columns in (("videos", ("cover_url", "media_url")), ("gifts", ("image_url",))):
        for column in columns:
            rows = connection.execute(
                f"SELECT {column} FROM {table} WHERE {column} LIKE 'oss://%'"
            ).fetchall()
            references.update(str(row[0]) for row in rows if row[0])
    for row in connection.execute("SELECT image_url, image_urls FROM gifts"):
        references.update(parse_gift_image_refs(row["image_urls"], row["image_url"]))
    return references


def reconcile_media_assets(connection: sqlite3.Connection) -> tuple[list[dict], dict]:
    if not oss_enabled():
        raise ApiError("OSS_NOT_CONFIGURED", "对象存储尚未配置", HTTPStatus.SERVICE_UNAVAILABLE)
    now = utc_now()
    objects = {item["storageRef"]: item for item in list_media_objects()}
    references = media_reference_set(connection)
    for storage_ref, item in objects.items():
        connection.execute(
            """INSERT OR IGNORE INTO media_assets
               (storage_ref, kind, original_name, size_bytes, status, created_at)
               VALUES (?, ?, ?, ?, 'UNBOUND', ?)""",
            (
                storage_ref,
                item["kind"],
                storage_ref.rsplit("/", 1)[-1],
                item["size"],
                item["lastModified"],
            ),
        )
        connection.execute(
            "UPDATE media_assets SET size_bytes=?, last_checked_at=? WHERE storage_ref=?",
            (item["size"], now, storage_ref),
        )

    rows = connection.execute("SELECT * FROM media_assets WHERE status <> 'DELETED'").fetchall()
    for row in rows:
        storage_ref = str(row["storage_ref"])
        exists = storage_ref in objects
        if storage_ref in references:
            status = "BOUND" if exists else "MISSING"
            connection.execute(
                """UPDATE media_assets SET status=?, cleanup_requested_at=NULL,
                   delete_after=NULL, last_checked_at=? WHERE storage_ref=?""",
                (status, now, storage_ref),
            )
        elif exists and row["status"] in {"PENDING_DELETE", "READY_DELETE"} and row["delete_after"]:
            status = "READY_DELETE" if str(row["delete_after"]) <= now else "PENDING_DELETE"
            connection.execute(
                "UPDATE media_assets SET status=?, last_checked_at=? WHERE storage_ref=?",
                (status, now, storage_ref),
            )
        elif exists:
            connection.execute(
                """UPDATE media_assets SET status='UNBOUND', cleanup_requested_at=NULL,
                   delete_after=NULL, last_checked_at=? WHERE storage_ref=?""",
                (now, storage_ref),
            )
        elif row["status"] != "UPLOAD_PENDING":
            connection.execute(
                "UPDATE media_assets SET status='MISSING', last_checked_at=? WHERE storage_ref=?",
                (now, storage_ref),
            )

    items = [
        dict(row)
        for row in connection.execute(
            """SELECT * FROM media_assets WHERE status <> 'DELETED'
               ORDER BY created_at DESC, storage_ref"""
        )
    ]
    summary = {key: 0 for key in ("total", "bound", "unbound", "pending", "ready", "missing")}
    summary["total"] = len(items)
    for item in items:
        status = item["status"]
        if status == "BOUND":
            summary["bound"] += 1
        elif status in {"UNBOUND", "UPLOAD_PENDING"}:
            summary["unbound"] += 1
        elif status == "PENDING_DELETE":
            summary["pending"] += 1
        elif status == "READY_DELETE":
            summary["ready"] += 1
        elif status == "MISSING":
            summary["missing"] += 1
    return items, summary


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class AppHandler(BaseHTTPRequestHandler):
    server_version = "KnowledgePointsSystem/0.2"

    def log_message(self, format_string: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def send_json(self, payload: dict | list, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def send_csv(self, content: str, filename: str) -> None:
        body = content.encode("utf-8-sig")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if self.path.startswith("/api/admin/uploads/videos"):
            limit = MAX_VIDEO_BYTES * 2
        elif self.path.startswith("/api/admin/uploads/images"):
            limit = MAX_IMAGE_BYTES * 2
        else:
            limit = MAX_JSON_BYTES
        if length > limit:
            raise ApiError("PAYLOAD_TOO_LARGE", "请求内容过大", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError("INVALID_JSON", "请求内容不是有效JSON") from exc

    def require_staff(self, allowed_roles: set[str] | None = None) -> dict:
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise ApiError("UNAUTHORIZED", "请先登录后台", HTTPStatus.UNAUTHORIZED)
        raw_token = authorization[7:].strip()
        with database() as connection:
            row = connection.execute(
                """SELECT u.id, u.username, u.display_name, u.role, u.store_id, u.status,
                          s.expires_at
                   FROM staff_sessions s
                   JOIN staff_users u ON u.id = s.staff_id
                   WHERE s.token_hash = ?""",
                (token_hash(raw_token),),
            ).fetchone()
            if not row or row["status"] != "ACTIVE" or row["expires_at"] <= utc_now():
                raise ApiError("UNAUTHORIZED", "登录已失效，请重新登录", HTTPStatus.UNAUTHORIZED)
            staff = dict(row)
        if allowed_roles and staff["role"] not in allowed_roles:
            raise ApiError("FORBIDDEN", "当前账号无权执行该操作", HTTPStatus.FORBIDDEN)
        return staff

    def require_user(self) -> dict:
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise ApiError("UNAUTHORIZED", "请先完成微信登录", HTTPStatus.UNAUTHORIZED)
        with database() as connection:
            row = connection.execute(
                """SELECT u.* FROM user_sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND s.expires_at>?""",
                (token_hash(authorization[7:].strip()), utc_now()),
            ).fetchone()
        if not row:
            raise ApiError("UNAUTHORIZED", "用户登录已失效", HTTPStatus.UNAUTHORIZED)
        if row["status"] == "DISABLED":
            raise ApiError("USER_DISABLED", "会员账号已停用", HTTPStatus.FORBIDDEN)
        return dict(row)

    def current_user_id(self, body: dict | None = None, parsed=None) -> int:
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            return int(self.require_user()["id"])
        if DEV_ENDPOINTS_ENABLED:
            if parsed is not None:
                raw = parse_qs(parsed.query).get("userId", ["1"])[0]
                return int(raw)
            return int((body or {}).get("userId", 1))
        raise ApiError("UNAUTHORIZED", "请先完成微信登录", HTTPStatus.UNAUTHORIZED)

    @staticmethod
    def write_admin_log(
        connection: sqlite3.Connection,
        staff_id: int,
        action: str,
        target_type: str,
        target_id: str | int,
        details: dict | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO admin_operation_logs
               (staff_id, action, target_type, target_id, details)
               VALUES (?, ?, ?, ?, ?)""",
            (
                staff_id,
                action,
                target_type,
                str(target_id),
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )

    def handle_api(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        method = self.command
        body = self.read_json() if method in {"POST", "PUT", "PATCH"} else {}

        if method == "GET" and path == "/api/health":
            self.send_json({"status": "ok", "database": str(DB_PATH.name)})
            return
        if method == "GET" and path == "/api/snapshot":
            user_id = self.current_user_id(parsed=parsed)
            self.send_json(snapshot(user_id))
            return
        if method == "POST" and path == "/api/auth/dev-login":
            if not DEV_ENDPOINTS_ENABLED:
                raise ApiError("NOT_FOUND", "接口不存在", HTTPStatus.NOT_FOUND)
            self.user_dev_login(body)
            return
        if method == "POST" and path == "/api/auth/wechat-login":
            self.user_wechat_login(body)
            return
        if method == "POST" and path == "/api/dev/reset":
            if not DEV_ENDPOINTS_ENABLED:
                raise ApiError("NOT_FOUND", "接口不存在", HTTPStatus.NOT_FOUND)
            initialize_database(reset=True)
            self.send_json({"ok": True, "message": "测试数据已重置"})
            return
        if method == "POST" and path == "/api/admin/login":
            self.admin_login(body)
            return
        if method == "POST" and path == "/api/admin/logout":
            self.admin_logout()
            return
        if method == "GET" and path == "/api/admin/me":
            self.send_json({"staff": self.require_staff()})
            return
        if method == "POST" and path == "/api/admin/me/password":
            self.admin_change_password(body)
            return
        if method == "GET" and path == "/api/admin/videos":
            self.admin_list_videos()
            return
        if method == "POST" and path == "/api/admin/videos":
            self.admin_create_video(body)
            return
        match = re.fullmatch(r"/api/admin/videos/(\d+)", path)
        if method == "PATCH" and match:
            self.admin_update_video(int(match.group(1)), body)
            return
        if method == "DELETE" and match:
            self.admin_delete_video(int(match.group(1)))
            return
        if method == "POST" and path == "/api/admin/uploads/videos":
            self.admin_upload_video(body)
            return
        if method == "POST" and path == "/api/admin/uploads/presign":
            self.admin_presign_upload(body)
            return
        if method == "GET" and path == "/api/admin/storage/assets":
            self.admin_list_storage_assets()
            return
        if method == "POST" and path == "/api/admin/storage/assets/cleanup-request":
            self.admin_request_asset_cleanup(body)
            return
        if method == "POST" and path == "/api/admin/storage/assets/cleanup-cancel":
            self.admin_cancel_asset_cleanup(body)
            return
        if method == "POST" and path == "/api/admin/storage/assets/delete":
            self.admin_delete_storage_asset(body)
            return
        if method == "GET" and path == "/api/admin/users":
            self.admin_list_users()
            return
        match = re.fullmatch(r"/api/admin/users/(\d+)", path)
        if method == "GET" and match:
            self.admin_get_user(int(match.group(1)))
            return
        if method == "PATCH" and match:
            self.admin_update_user(int(match.group(1)), body)
            return
        if method == "GET" and path == "/api/admin/watch-details":
            self.admin_watch_details()
            return
        if method == "GET" and path == "/api/admin/point-transactions":
            self.admin_list_point_transactions(parsed)
            return
        if method == "POST" and path == "/api/admin/points/adjust":
            self.admin_adjust_points(body)
            return
        if method == "GET" and path == "/api/admin/gifts":
            self.admin_list_gifts()
            return
        if method == "POST" and path == "/api/admin/gifts":
            self.admin_create_gift(body)
            return
        if method == "POST" and path == "/api/admin/uploads/images":
            self.admin_upload_image(body)
            return
        match = re.fullmatch(r"/api/admin/gifts/(\d+)", path)
        if method == "PATCH" and match:
            self.admin_update_gift(int(match.group(1)), body)
            return
        match = re.fullmatch(r"/api/admin/inventory/(\d+)/(\d+)", path)
        if method == "PATCH" and match:
            self.admin_update_inventory(int(match.group(1)), int(match.group(2)), body)
            return
        if method == "GET" and path == "/api/admin/pickups":
            self.admin_list_pickups()
            return
        if method == "GET" and path == "/api/admin/invitations":
            self.admin_list_invitations()
            return
        if method == "GET" and path == "/api/admin/dashboard":
            self.admin_dashboard()
            return
        if method == "GET" and path == "/api/admin/configs":
            self.admin_list_configs()
            return
        if method == "PATCH" and path == "/api/admin/configs":
            self.admin_update_configs(body)
            return
        if method == "GET" and path == "/api/admin/page-descriptions":
            self.admin_list_page_descriptions()
            return
        if method == "PATCH" and path == "/api/admin/page-descriptions":
            self.admin_update_page_descriptions(body)
            return
        if method == "GET" and path == "/api/admin/logs":
            self.admin_list_logs()
            return
        if method == "GET" and path == "/api/admin/logs/export":
            self.admin_export_logs()
            return
        if method == "GET" and path == "/api/admin/staff":
            self.admin_list_staff()
            return
        if method == "POST" and path == "/api/admin/staff":
            self.admin_create_staff(body)
            return
        match = re.fullmatch(r"/api/admin/staff/(\d+)", path)
        if method == "PATCH" and match:
            self.admin_update_staff(int(match.group(1)), body)
            return
        if method == "GET" and path == "/api/admin/stores":
            self.admin_list_stores()
            return
        if method == "POST" and path == "/api/admin/stores":
            self.admin_create_store(body)
            return
        match = re.fullmatch(r"/api/admin/stores/(\d+)", path)
        if method == "PATCH" and match:
            self.admin_update_store(int(match.group(1)), body)
            return
        if method == "POST" and path == "/api/watch/sessions":
            self.create_watch_session(body)
            return
        if method == "POST" and path == "/api/watch/heartbeat":
            self.submit_watch_heartbeat(body)
            return
        if method == "POST" and path == "/api/watch/complete":
            self.complete_watch_session(body)
            return
        if method == "POST" and path == "/api/dev/watch-step":
            if not DEV_ENDPOINTS_ENABLED:
                raise ApiError("NOT_FOUND", "接口不存在", HTTPStatus.NOT_FOUND)
            self.dev_watch_step(body)
            return
        if method == "POST" and path == "/api/pickups":
            self.create_pickup(body)
            return
        match = re.fullmatch(r"/api/pickups/(\d+)/voucher-qr", path)
        if method == "POST" and match:
            self.create_pickup_voucher_qr(int(match.group(1)), body)
            return
        match = re.fullmatch(r"/api/pickups/(\d+)/cancel", path)
        if method == "POST" and match:
            self.cancel_pickup(int(match.group(1)), body)
            return
        match = re.fullmatch(r"/api/staff/pickups/(\d+)/(contacted|verify|reject)", path)
        if method == "POST" and match:
            self.staff_pickup_action(int(match.group(1)), match.group(2), body, self.require_staff())
            return
        if method == "POST" and path == "/api/invitations/create-invitee":
            self.create_invitee(body)
            return
        if method == "POST" and path == "/api/invitations/mini-code":
            self.create_invitation_mini_code()
            return
        raise ApiError("NOT_FOUND", "接口不存在", HTTPStatus.NOT_FOUND)

    def admin_login(self, body: dict) -> None:
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        with database() as connection:
            staff = connection.execute(
                "SELECT * FROM staff_users WHERE username = ? AND status = 'ACTIVE'", (username,)
            ).fetchone()
            supplied_hash = password_hash(password, staff["password_salt"]) if staff else ""
            if not staff or not hmac.compare_digest(supplied_hash, staff["password_hash"]):
                raise ApiError("LOGIN_FAILED", "账号或密码错误", HTTPStatus.UNAUTHORIZED)
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat(
                timespec="seconds"
            )
            connection.execute("DELETE FROM staff_sessions WHERE expires_at <= ?", (utc_now(),))
            connection.execute(
                "INSERT INTO staff_sessions (token_hash, staff_id, expires_at) VALUES (?, ?, ?)",
                (token_hash(token), staff["id"], expires_at),
            )
            self.write_admin_log(connection, int(staff["id"]), "LOGIN", "SESSION", "self")
        self.send_json(
            {
                "token": token,
                "expiresAt": expires_at,
                "staff": {
                    "id": staff["id"],
                    "username": staff["username"],
                    "displayName": staff["display_name"],
                    "role": staff["role"],
                    "storeId": staff["store_id"],
                },
            }
        )

    def issue_user_session(self, user_id: int, invitation_bound: bool = False) -> None:
        raw_token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")
        with database() as connection:
            connection.execute("DELETE FROM user_sessions WHERE expires_at<=?", (utc_now(),))
            connection.execute(
                "INSERT INTO user_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash(raw_token), user_id, expires_at),
            )
            user = dict(connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        self.send_json({
            "token": raw_token,
            "expiresAt": expires_at,
            "user": user,
            "invitationBound": invitation_bound,
        })

    def user_dev_login(self, body: dict) -> None:
        user_id = int(body.get("userId", 1))
        with database() as connection:
            if not connection.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                raise ApiError("USER_NOT_FOUND", "测试用户不存在", HTTPStatus.NOT_FOUND)
        self.issue_user_session(user_id)

    def user_wechat_login(self, body: dict) -> None:
        code = str(body.get("code", "")).strip()
        invite_code = str(body.get("inviteCode", "")).strip()
        invite_source = str(body.get("inviteSource", "SHARE_LINK")).strip()
        source_value = str(body.get("sourceValue", invite_code)).strip()
        landing_scene = str(body.get("landingScene", "")).strip()
        app_id = os.environ.get("WX_APPID", "")
        app_secret = os.environ.get("WX_APPSECRET", "")
        if not code or not app_id or not app_secret:
            raise ApiError("WECHAT_LOGIN_NOT_CONFIGURED", "微信登录参数尚未配置", HTTPStatus.SERVICE_UNAVAILABLE)
        query = urlencode({"appid": app_id, "secret": app_secret, "js_code": code, "grant_type": "authorization_code"})
        try:
            with urlopen(f"https://api.weixin.qq.com/sns/jscode2session?{query}", timeout=8) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ApiError("WECHAT_LOGIN_FAILED", "微信登录服务暂时不可用", HTTPStatus.BAD_GATEWAY) from exc
        openid = result.get("openid")
        if not openid:
            raise ApiError("WECHAT_LOGIN_FAILED", "微信登录凭证无效", HTTPStatus.UNAUTHORIZED)
        invitation_bound = False
        with database() as connection:
            user = connection.execute("SELECT id FROM users WHERE wechat_openid=?", (openid,)).fetchone()
            if user:
                user_id = int(user["id"])
            else:
                cursor = connection.execute(
                    "INSERT INTO users (wechat_openid, name, invite_code, points) VALUES (?, ?, ?, 0)",
                    (openid, "微信用户", secrets.token_hex(4).upper()),
                )
                user_id = int(cursor.lastrowid)
                invitation_bound = bind_invitation_by_code(
                    connection,
                    user_id,
                    invite_code,
                    invite_source,
                    source_value,
                    landing_scene,
                )
        self.issue_user_session(user_id, invitation_bound=invitation_bound)

    def admin_logout(self) -> None:
        staff = self.require_staff()
        raw_token = self.headers.get("Authorization", "")[7:].strip()
        with database() as connection:
            connection.execute("DELETE FROM staff_sessions WHERE token_hash = ?", (token_hash(raw_token),))
            self.write_admin_log(connection, int(staff["id"]), "LOGOUT", "SESSION", "self")
        self.send_json({"ok": True})

    def admin_list_videos(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM videos ORDER BY id DESC")]
        for row in rows:
            row["cover_storage_ref"] = row["cover_url"]
            row["media_storage_ref"] = row["media_url"]
            row["cover_url"] = resolve_storage_url(row["cover_url"])
            row["media_url"] = resolve_storage_url(row["media_url"])
        self.send_json({"items": rows})

    @staticmethod
    def normalize_video_input(body: dict, existing: sqlite3.Row | None = None) -> dict:
        def value(api_key: str, db_key: str | None = None, default=None):
            if api_key in body:
                return body[api_key]
            if db_key and db_key in body:
                return body[db_key]
            return existing[db_key or api_key] if existing is not None else default

        item = {
            "title": str(value("title", default="")).strip(),
            "summary": str(value("summary", default="")).strip(),
            "cover_url": str(value("coverUrl", "cover_url", "/assets/video-conference.svg")).strip(),
            "media_url": str(value("mediaUrl", "media_url", "")).strip(),
            "category": str(value("category", default="Health Knowledge")).strip(),
            "tags": str(value("tags", default="")).strip(),
            "duration_seconds": int(value("durationSeconds", "duration_seconds", 0)),
            "total_points": int(value("totalPoints", "total_points", -1)),
            "status": str(value("status", default="DRAFT")).upper(),
        }
        if item["total_points"] == -1 and existing is None and "totalPoints" not in body and "total_points" not in body:
            item["total_points"] = calculate_video_points(item["duration_seconds"])
        if not item["title"] or not item["summary"] or not item["cover_url"] or not item["media_url"] or not item["category"]:
            raise ApiError("INVALID_VIDEO_INPUT", "标题、简介、分类、封面和视频地址不能为空")
        if item["duration_seconds"] <= 0:
            raise ApiError("INVALID_VIDEO_INPUT", "视频时长必须大于0")
        if item["total_points"] < 0:
            raise ApiError("INVALID_VIDEO_INPUT", "视频总积分不能小于0")
        if len(item["tags"]) > 200:
            raise ApiError("INVALID_VIDEO_INPUT", "视频标签过长")
        if not item["cover_url"].startswith(("/assets/", "/uploads/", "https://", "oss://")):
            raise ApiError("INVALID_VIDEO_INPUT", "封面必须是已上传图片或HTTPS地址")
        if not item["media_url"].startswith(("/media/", "/uploads/", "https://", "oss://")):
            raise ApiError("INVALID_VIDEO_INPUT", "视频必须是已上传文件或HTTPS地址")
        if item["status"] not in {"DRAFT", "PUBLISHED", "OFFLINE"}:
            raise ApiError("INVALID_VIDEO_INPUT", "视频状态设置不正确")
        return item

    def admin_create_video(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        item = self.normalize_video_input(body)
        with database() as connection:
            cursor = connection.execute(
                """INSERT INTO videos
                   (title, summary, cover_url, media_url, category, tags, duration_seconds,
                    total_points, status)
                   VALUES (:title, :summary, :cover_url, :media_url, :category, :tags,
                           :duration_seconds, :total_points, :status)""",
                item,
            )
            video_id = int(cursor.lastrowid)
            self.write_admin_log(connection, int(staff["id"]), "CREATE", "VIDEO", video_id, item)
            created = dict(connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone())
        self.send_json(created, HTTPStatus.CREATED)

    def admin_update_video(self, video_id: int, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        with database() as connection:
            existing = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if not existing:
                raise ApiError("VIDEO_NOT_FOUND", "视频不存在", HTTPStatus.NOT_FOUND)
            item = self.normalize_video_input(body, existing)
            connection.execute(
                """UPDATE videos SET title = :title, summary = :summary, cover_url = :cover_url,
                   media_url = :media_url, category = :category, tags = :tags, duration_seconds = :duration_seconds,
                   total_points = :total_points, status = :status WHERE id = :id""",
                {**item, "id": video_id},
            )
            self.write_admin_log(connection, int(staff["id"]), "UPDATE", "VIDEO", video_id, item)
            updated = dict(connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone())
        self.send_json(updated)

    def admin_delete_video(self, video_id: int) -> None:
        staff = self.require_staff({"ADMIN"})
        with database() as connection:
            existing = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if not existing:
                raise ApiError("VIDEO_NOT_FOUND", "视频不存在", HTTPStatus.NOT_FOUND)
            has_history = connection.execute(
                """SELECT EXISTS(SELECT 1 FROM watch_progress WHERE video_id=?) OR
                          EXISTS(SELECT 1 FROM watch_sessions WHERE video_id=?) OR
                          EXISTS(SELECT 1 FROM watch_ticks WHERE video_id=?)""",
                (video_id, video_id, video_id),
            ).fetchone()[0]
            if has_history:
                raise ApiError(
                    "VIDEO_HAS_HISTORY",
                    "该视频已有观看记录，为保留用户历史只能下架，不能删除",
                    HTTPStatus.CONFLICT,
                )
            self.write_admin_log(
                connection, int(staff["id"]), "DELETE", "VIDEO", video_id, dict(existing)
            )
            connection.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        self.send_json({"ok": True, "id": video_id})

    def admin_upload_video(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        data_url = str(body.get("dataUrl", ""))
        match = re.fullmatch(r"data:video/(mp4|webm);base64,([A-Za-z0-9+/=\r\n]+)", data_url)
        if not match:
            raise ApiError("INVALID_VIDEO_FILE", "请选择 MP4 或 WebM 视频")
        try:
            video_bytes = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ApiError("INVALID_VIDEO_FILE", "视频内容无法读取") from exc
        if not video_bytes or len(video_bytes) > MAX_VIDEO_BYTES:
            raise ApiError("INVALID_VIDEO_FILE", "视频不能为空且不能超过50MB")
        video_type = match.group(1)
        valid_signature = (
            len(video_bytes) > 12 and video_bytes[4:8] == b"ftyp"
            if video_type == "mp4"
            else video_bytes.startswith(b"\x1aE\xdf\xa3")
        )
        if not valid_signature:
            raise ApiError("INVALID_VIDEO_FILE", "视频格式与文件内容不一致")
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        filename = f"video-{uuid.uuid4().hex}.{video_type}"
        (UPLOAD_ROOT / filename).write_bytes(video_bytes)
        video_url = f"/uploads/{filename}"
        with database() as connection:
            self.write_admin_log(
                connection,
                int(staff["id"]),
                "UPLOAD",
                "VIDEO_FILE",
                filename,
                {"bytes": len(video_bytes)},
            )
        self.send_json({"url": video_url, "size": len(video_bytes)}, HTTPStatus.CREATED)

    def admin_presign_upload(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        if not oss_enabled():
            raise ApiError("OSS_NOT_CONFIGURED", "对象存储尚未配置", HTTPStatus.SERVICE_UNAVAILABLE)
        kind = str(body.get("kind", "")).strip().lower()
        filename = str(body.get("filename", "")).strip()
        content_type = str(body.get("contentType", "")).strip().lower()
        try:
            size = int(body.get("size", 0))
            result = new_oss_upload(kind, filename, content_type, size)
        except (TypeError, ValueError) as exc:
            raise ApiError(
                "INVALID_UPLOAD",
                "视频最大5GB，图片最大5MB，仅支持MP4、WebM、PNG、JPG或WebP",
            ) from exc
        with database() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO media_assets
                   (storage_ref, kind, original_name, content_type, size_bytes, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'UPLOAD_PENDING', ?)""",
                (result["storageRef"], kind.upper(), filename, content_type, size, utc_now()),
            )
            self.write_admin_log(
                connection,
                int(staff["id"]),
                "CREATE_UPLOAD_URL",
                kind.upper(),
                result["storageRef"],
                {"filename": filename, "contentType": content_type, "bytes": size},
            )
        self.send_json(result, HTTPStatus.CREATED)

    def admin_list_storage_assets(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            items, summary = reconcile_media_assets(connection)
        self.send_json({"items": items, "summary": summary, "cleanupGraceDays": MEDIA_CLEANUP_GRACE_DAYS})

    def admin_request_asset_cleanup(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        storage_ref = str(body.get("storageRef", "")).strip()
        with database() as connection:
            reconcile_media_assets(connection)
            row = connection.execute(
                "SELECT * FROM media_assets WHERE storage_ref=?", (storage_ref,)
            ).fetchone()
            if not row or row["status"] != "UNBOUND":
                raise ApiError("ASSET_NOT_UNBOUND", "只有已上传且未被使用的文件可以加入清理队列", HTTPStatus.CONFLICT)
            requested_at = utc_now()
            delete_after = (
                datetime.now(timezone.utc) + timedelta(days=MEDIA_CLEANUP_GRACE_DAYS)
            ).isoformat(timespec="seconds")
            connection.execute(
                """UPDATE media_assets SET status='PENDING_DELETE', cleanup_requested_at=?,
                   delete_after=? WHERE storage_ref=?""",
                (requested_at, delete_after, storage_ref),
            )
            self.write_admin_log(
                connection, int(staff["id"]), "REQUEST_CLEANUP", "OSS_FILE", storage_ref,
                {"deleteAfter": delete_after},
            )
        self.send_json({"ok": True, "storageRef": storage_ref, "deleteAfter": delete_after})

    def admin_cancel_asset_cleanup(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        storage_ref = str(body.get("storageRef", "")).strip()
        with database() as connection:
            row = connection.execute(
                "SELECT * FROM media_assets WHERE storage_ref=?", (storage_ref,)
            ).fetchone()
            if not row or row["status"] not in {"PENDING_DELETE", "READY_DELETE"}:
                raise ApiError("ASSET_NOT_PENDING", "该文件不在清理队列中", HTTPStatus.CONFLICT)
            connection.execute(
                """UPDATE media_assets SET status='UNBOUND', cleanup_requested_at=NULL,
                   delete_after=NULL WHERE storage_ref=?""",
                (storage_ref,),
            )
            self.write_admin_log(
                connection, int(staff["id"]), "CANCEL_CLEANUP", "OSS_FILE", storage_ref
            )
        self.send_json({"ok": True, "storageRef": storage_ref})

    def admin_delete_storage_asset(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        storage_ref = str(body.get("storageRef", "")).strip()
        with database() as connection:
            reconcile_media_assets(connection)
            row = connection.execute(
                "SELECT * FROM media_assets WHERE storage_ref=?", (storage_ref,)
            ).fetchone()
            if not row or row["status"] != "READY_DELETE":
                raise ApiError("ASSET_NOT_READY", "文件尚未经过7天等待期，不能删除", HTTPStatus.CONFLICT)
            if storage_ref in media_reference_set(connection):
                connection.execute(
                    """UPDATE media_assets SET status='BOUND', cleanup_requested_at=NULL,
                       delete_after=NULL WHERE storage_ref=?""",
                    (storage_ref,),
                )
                raise ApiError("ASSET_IN_USE", "删除前复核发现文件正在使用，已自动取消清理", HTTPStatus.CONFLICT)
            delete_storage_object(storage_ref)
            deleted_at = utc_now()
            connection.execute(
                """UPDATE media_assets SET status='DELETED', deleted_at=?,
                   last_checked_at=? WHERE storage_ref=?""",
                (deleted_at, deleted_at, storage_ref),
            )
            self.write_admin_log(
                connection, int(staff["id"]), "DELETE", "OSS_FILE", storage_ref,
                {"deletedAt": deleted_at},
            )
        self.send_json({"ok": True, "storageRef": storage_ref, "deletedAt": deleted_at})

    def admin_list_users(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT u.id, u.name, u.mobile, u.tags, u.notes, u.status,
                              u.invite_code, u.points, u.created_at,
                              COALESCE(w.valid_watch_seconds, 0) AS valid_watch_seconds,
                              COALESCE(w.watched_video_count, 0) AS watched_video_count,
                              COALESCE(p.pickup_count, 0) AS pickup_count,
                              COALESCE(children.invite_count, 0) AS invite_count,
                              parent.inviter_user_id, inviter.name AS inviter_name,
                              inviter.invite_code AS inviter_code,
                              parent.status AS invitation_status,
                              parent.source_type, parent.source_value, parent.landing_scene
                       FROM users u
                       LEFT JOIN (
                         SELECT user_id, SUM(valid_seconds) AS valid_watch_seconds,
                                COUNT(*) AS watched_video_count
                         FROM watch_progress GROUP BY user_id
                       ) w ON w.user_id = u.id
                       LEFT JOIN (
                         SELECT user_id, COUNT(*) AS pickup_count
                         FROM pickup_requests GROUP BY user_id
                       ) p ON p.user_id = u.id
                       LEFT JOIN (
                         SELECT inviter_user_id, COUNT(*) AS invite_count
                         FROM invitations GROUP BY inviter_user_id
                       ) children ON children.inviter_user_id = u.id
                       LEFT JOIN invitations parent ON parent.invitee_user_id = u.id
                       LEFT JOIN users inviter ON inviter.id = parent.inviter_user_id
                       ORDER BY u.id DESC"""
                )
            ]
        self.send_json({"items": rows})

    def admin_get_user(self, user_id: int) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            user = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise ApiError("USER_NOT_FOUND", "会员不存在", HTTPStatus.NOT_FOUND)
            watch = [dict(row) for row in connection.execute(
                """SELECT w.*, v.title AS video_title, v.duration_seconds
                   FROM watch_progress w JOIN videos v ON v.id=w.video_id
                   WHERE w.user_id=? ORDER BY w.last_watched_at DESC""", (user_id,)
            )]
            transactions = [dict(row) for row in connection.execute(
                "SELECT * FROM point_transactions WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,)
            )]
            pickups = [dict(row) for row in connection.execute(
                """SELECT p.*, g.name AS gift_name, s.name AS store_name
                   FROM pickup_requests p JOIN gifts g ON g.id=p.gift_id
                   JOIN stores s ON s.id=p.store_id WHERE p.user_id=? ORDER BY p.id DESC""", (user_id,)
            )]
            invitation = row_dict(connection.execute(
                """SELECT i.*, u.name AS inviter_name, u.invite_code AS inviter_code
                   FROM invitations i JOIN users u ON u.id=i.inviter_user_id
                   WHERE i.invitee_user_id=?""", (user_id,)
            ).fetchone())
            invitees = [dict(row) for row in connection.execute(
                """SELECT i.*, u.name AS invitee_name, u.mobile AS invitee_mobile,
                          u.points AS invitee_points
                   FROM invitations i JOIN users u ON u.id=i.invitee_user_id
                   WHERE i.inviter_user_id=? ORDER BY i.id DESC""", (user_id,)
            )]
        self.send_json({
            "user": dict(user), "watch": watch, "transactions": transactions,
            "pickups": pickups, "invitation": invitation, "invitees": invitees,
        })

    def admin_update_user(self, user_id: int, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        name = str(body.get("name", "")).strip()
        mobile = str(body.get("mobile", "")).strip()
        tags = str(body.get("tags", "")).strip()
        notes = str(body.get("notes", "")).strip()
        status = str(body.get("status", "ACTIVE")).upper()
        if not name or len(name) > 50:
            raise ApiError("INVALID_USER_INPUT", "会员姓名不能为空且不能超过50个字")
        if mobile and not re.fullmatch(r"[0-9+\- ]{6,20}", mobile):
            raise ApiError("INVALID_USER_INPUT", "手机号格式不正确")
        if len(tags) > 200 or len(notes) > 1000 or status not in {"ACTIVE", "DISABLED"}:
            raise ApiError("INVALID_USER_INPUT", "会员标签、备注或状态设置不正确")
        with database() as connection:
            existing = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not existing:
                raise ApiError("USER_NOT_FOUND", "会员不存在", HTTPStatus.NOT_FOUND)
            item = {"name": name, "mobile": mobile, "tags": tags, "notes": notes, "status": status}
            connection.execute(
                """UPDATE users SET name=:name, mobile=:mobile, tags=:tags,
                   notes=:notes, status=:status WHERE id=:id""", {**item, "id": user_id}
            )
            if status == "DISABLED":
                connection.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            self.write_admin_log(connection, int(staff["id"]), "UPDATE", "USER", user_id, item)
            updated = dict(connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        self.send_json(updated)

    def admin_watch_details(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            rows = [dict(row) for row in connection.execute(
                """SELECT w.user_id, u.name AS user_name, u.mobile, u.status AS user_status,
                          w.video_id, v.title AS video_title, v.category, v.status AS video_status,
                          v.duration_seconds, v.total_points, w.valid_seconds, w.rewarded_points,
                          w.last_watched_at, COUNT(t.id) AS heartbeat_count,
                          w.rewarded_points AS earned_points
                   FROM watch_progress w
                   JOIN users u ON u.id=w.user_id JOIN videos v ON v.id=w.video_id
                   LEFT JOIN watch_ticks t ON t.user_id=w.user_id AND t.video_id=w.video_id
                   GROUP BY w.user_id, w.video_id ORDER BY w.last_watched_at DESC, w.valid_seconds DESC"""
            )]
        for row in rows:
            row["completion_percent"] = min(100, round(row["valid_seconds"] * 100 / row["duration_seconds"], 1))
        self.send_json({
            "items": rows,
            "summary": {
                "totalValidSeconds": sum(row["valid_seconds"] for row in rows),
                "activeUsers": len({row["user_id"] for row in rows}),
                "watchedVideos": len({row["video_id"] for row in rows}),
                "rewardPoints": sum(row["earned_points"] for row in rows),
            },
        })

    def admin_list_point_transactions(self, parsed) -> None:
        self.require_staff({"ADMIN"})
        raw_user_id = parse_qs(parsed.query).get("userId", [""])[0]
        with database() as connection:
            if raw_user_id:
                rows = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT t.*, u.name AS user_name FROM point_transactions t
                           JOIN users u ON u.id = t.user_id
                           WHERE t.user_id = ? ORDER BY t.id DESC LIMIT 200""",
                        (int(raw_user_id),),
                    )
                ]
            else:
                rows = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT t.*, u.name AS user_name FROM point_transactions t
                           JOIN users u ON u.id = t.user_id ORDER BY t.id DESC LIMIT 200"""
                    )
                ]
        self.send_json({"items": rows})

    def admin_adjust_points(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        user_id = int(body.get("userId", 0))
        amount = int(body.get("amount", 0))
        reason = str(body.get("reason", "")).strip()
        request_key = str(body.get("requestId", "")).strip()
        if amount == 0 or not reason or not request_key:
            raise ApiError("INVALID_POINT_ADJUSTMENT", "积分数量、调整原因和请求编号不能为空")
        if abs(amount) > 100_000:
            raise ApiError("INVALID_POINT_ADJUSTMENT", "单次调整积分超过安全范围")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = replay_idempotent(
                connection, "STAFF", int(staff["id"]), "admin-point-adjust", request_key
            )
            if replay is not None:
                connection.rollback()
                self.send_json(replay)
                return
            balance = add_points(
                connection,
                user_id,
                amount,
                "MANUAL_ADJUST",
                f"admin:{staff['id']}:{request_key}",
                reason,
            )
            result = {"userId": user_id, "amount": amount, "balance": balance}
            save_idempotent(
                connection, "STAFF", int(staff["id"]), "admin-point-adjust", request_key, result
            )
            self.write_admin_log(
                connection,
                int(staff["id"]),
                "ADJUST_POINTS",
                "USER",
                user_id,
                {"amount": amount, "reason": reason, "balance": balance},
            )
            connection.commit()
        self.send_json(result)

    def admin_list_gifts(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            stores = [dict(row) for row in connection.execute("SELECT * FROM stores ORDER BY id")]
            gifts = [
                dict(row)
                for row in connection.execute(
                    """SELECT g.*, i.store_id, s.name AS store_name, i.available_quantity,
                              i.locked_quantity, i.redeemed_quantity
                       FROM gifts g
                       LEFT JOIN store_gift_inventory i ON i.gift_id = g.id
                       LEFT JOIN stores s ON s.id = i.store_id ORDER BY g.id DESC"""
                )
            ]
        for gift in gifts:
            prepare_gift_images(gift, include_storage_refs=True)
        self.send_json({"items": gifts, "stores": stores})

    @staticmethod
    def normalize_gift_input(body: dict, existing: sqlite3.Row | None = None) -> dict:
        def pick(api_key: str, db_key: str | None = None, default=None):
            if api_key in body:
                return body[api_key]
            return existing[db_key or api_key] if existing is not None else default

        image_refs = parse_gift_image_refs(
            pick("imageUrls", "image_urls", None),
            str(pick("imageUrl", "image_url", "")).strip(),
        )
        item = {
            "name": str(pick("name", default="")).strip(),
            "description": str(pick("description", default="")).strip(),
            "market_price": int(pick("marketPrice", "market_price", 0)),
            "image_key": str(pick("imageKey", "image_key", "gift")).strip(),
            "image_url": image_refs[0] if image_refs else "",
            "image_urls": json.dumps(image_refs, ensure_ascii=False),
            "points_cost": int(pick("pointsCost", "points_cost", 0)),
            "status": str(pick("status", default="ACTIVE")).upper(),
        }
        if not item["name"] or not item["description"] or item["points_cost"] <= 0:
            raise ApiError("INVALID_GIFT_INPUT", "礼品名称、说明和兑换积分设置不正确")
        if any(not ref.startswith(("/assets/", "/uploads/", "https://", "oss://")) for ref in image_refs):
            raise ApiError("INVALID_GIFT_INPUT", "礼品图片必须是已上传图片或 HTTPS 地址")
        if not image_refs:
            raise ApiError("INVALID_GIFT_INPUT", "请至少上传一张礼品图片")
        if item["market_price"] < 0 or item["status"] not in {"ACTIVE", "INACTIVE"}:
            raise ApiError("INVALID_GIFT_INPUT", "礼品价格或状态设置不正确")
        return item

    def admin_create_gift(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        item = self.normalize_gift_input(body)
        store_id = int(body.get("storeId", 1))
        available = int(body.get("availableQuantity", 0))
        if available < 0:
            raise ApiError("INVALID_INVENTORY", "库存不能为负数")
        with database() as connection:
            if not connection.execute("SELECT 1 FROM stores WHERE id = ?", (store_id,)).fetchone():
                raise ApiError("STORE_NOT_FOUND", "门店不存在", HTTPStatus.NOT_FOUND)
            cursor = connection.execute(
                """INSERT INTO gifts (name, description, market_price, image_key, image_url, image_urls, points_cost, status)
                   VALUES (:name, :description, :market_price, :image_key, :image_url, :image_urls, :points_cost, :status)""",
                item,
            )
            gift_id = int(cursor.lastrowid)
            connection.execute(
                """INSERT INTO store_gift_inventory
                   (store_id, gift_id, available_quantity) VALUES (?, ?, ?)""",
                (store_id, gift_id, available),
            )
            self.write_admin_log(connection, int(staff["id"]), "CREATE", "GIFT", gift_id, item)
        self.send_json({"id": gift_id, **item, "storeId": store_id, "availableQuantity": available}, HTTPStatus.CREATED)

    def admin_update_gift(self, gift_id: int, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        with database() as connection:
            existing = connection.execute("SELECT * FROM gifts WHERE id = ?", (gift_id,)).fetchone()
            if not existing:
                raise ApiError("GIFT_NOT_FOUND", "礼品不存在", HTTPStatus.NOT_FOUND)
            item = self.normalize_gift_input(body, existing)
            connection.execute(
                """UPDATE gifts SET name=:name, description=:description, market_price=:market_price,
                   image_key=:image_key, image_url=:image_url, image_urls=:image_urls, points_cost=:points_cost,
                   status=:status WHERE id=:id""",
                {**item, "id": gift_id},
            )
            self.write_admin_log(connection, int(staff["id"]), "UPDATE", "GIFT", gift_id, item)
        self.send_json({"id": gift_id, **item})

    def admin_upload_image(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        data_url = str(body.get("dataUrl", ""))
        match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)", data_url)
        if not match:
            raise ApiError("INVALID_IMAGE", "请选择 PNG、JPG 或 WebP 图片")
        try:
            image_bytes = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ApiError("INVALID_IMAGE", "图片内容无法读取") from exc
        if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
            raise ApiError("INVALID_IMAGE", "图片不能为空且不能超过3MB")
        signatures = {
            "png": image_bytes.startswith(b"\x89PNG\r\n\x1a\n"),
            "jpeg": image_bytes.startswith(b"\xff\xd8\xff"),
            "webp": image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP",
        }
        image_type = match.group(1)
        if not signatures[image_type]:
            raise ApiError("INVALID_IMAGE", "图片格式与文件内容不一致")
        extension = "jpg" if image_type == "jpeg" else image_type
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        filename = f"gift-{uuid.uuid4().hex}.{extension}"
        (UPLOAD_ROOT / filename).write_bytes(image_bytes)
        image_url = f"/uploads/{filename}"
        with database() as connection:
            self.write_admin_log(connection, int(staff["id"]), "UPLOAD", "IMAGE", filename, {"bytes": len(image_bytes)})
        self.send_json({"url": image_url, "size": len(image_bytes)}, HTTPStatus.CREATED)

    def admin_update_inventory(self, store_id: int, gift_id: int, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        available = int(body.get("availableQuantity", -1))
        if available < 0:
            raise ApiError("INVALID_INVENTORY", "可用库存不能为负数")
        with database() as connection:
            inventory = connection.execute(
                "SELECT * FROM store_gift_inventory WHERE store_id=? AND gift_id=?",
                (store_id, gift_id),
            ).fetchone()
            if not inventory:
                raise ApiError("INVENTORY_NOT_FOUND", "库存记录不存在", HTTPStatus.NOT_FOUND)
            connection.execute(
                """UPDATE store_gift_inventory SET available_quantity=?
                   WHERE store_id=? AND gift_id=?""",
                (available, store_id, gift_id),
            )
            self.write_admin_log(
                connection, int(staff["id"]), "SET_INVENTORY", "GIFT", gift_id,
                {"storeId": store_id, "before": inventory["available_quantity"], "after": available},
            )
        self.send_json({"storeId": store_id, "giftId": gift_id, "availableQuantity": available})

    def admin_list_pickups(self) -> None:
        staff = self.require_staff()
        where = "WHERE p.store_id = ?" if staff["role"] == "STORE_VERIFIER" else ""
        params = (staff["store_id"],) if where else ()
        with database() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    f"""SELECT p.*, u.name AS user_name, g.name AS gift_name, s.name AS store_name,
                               l.status AS lead_status
                        FROM pickup_requests p JOIN users u ON u.id=p.user_id
                        JOIN gifts g ON g.id=p.gift_id JOIN stores s ON s.id=p.store_id
                        LEFT JOIN store_leads l ON l.pickup_id=p.id {where} ORDER BY p.id DESC""",
                    params,
                )
            ]
        self.send_json({"items": rows})

    def admin_list_invitations(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT i.*, a.name AS inviter_name, a.invite_code AS inviter_code,
                              b.name AS invitee_name, b.invite_code AS invitee_code
                       FROM invitations i JOIN users a ON a.id=i.inviter_user_id
                       JOIN users b ON b.id=i.invitee_user_id ORDER BY i.id DESC"""
                )
            ]
        self.send_json(
            {
                "items": rows,
                "summary": {
                    "total": len(rows),
                    "rewarded": sum(1 for item in rows if item["status"] == "REWARDED"),
                    "bound": sum(1 for item in rows if item["status"] == "BOUND"),
                },
            }
        )

    def admin_dashboard(self) -> None:
        self.require_staff()
        with database() as connection:
            counts = {
                "users": connection.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "publishedVideos": connection.execute("SELECT COUNT(*) FROM videos WHERE status='PUBLISHED'").fetchone()[0],
                "activeGifts": connection.execute("SELECT COUNT(*) FROM gifts WHERE status='ACTIVE'").fetchone()[0],
                "pendingPickups": connection.execute("SELECT COUNT(*) FROM pickup_requests WHERE status IN ('WAIT_CONTACT','WAIT_ARRIVAL')").fetchone()[0],
                "completedPickups": connection.execute("SELECT COUNT(*) FROM pickup_requests WHERE status='PICKED_UP'").fetchone()[0],
                "validWatchSeconds": connection.execute("SELECT COALESCE(SUM(valid_seconds),0) FROM watch_progress").fetchone()[0],
                "rewardedInvites": connection.execute("SELECT COUNT(*) FROM invitations WHERE status='REWARDED'").fetchone()[0],
            }
            recent = [
                dict(row)
                for row in connection.execute(
                    """SELECT p.request_no, u.name AS user_name, g.name AS gift_name, p.status, p.created_at
                       FROM pickup_requests p JOIN users u ON u.id=p.user_id
                       JOIN gifts g ON g.id=p.gift_id ORDER BY p.id DESC LIMIT 8"""
                )
            ]
        self.send_json({"counts": counts, "recentPickups": recent})

    def admin_list_configs(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM system_configs ORDER BY config_key")]
        self.send_json({"items": rows})

    def admin_list_page_descriptions(self) -> None:
        self.require_staff()
        with database() as connection:
            rows = connection.execute(
                "SELECT config_key, config_value FROM system_configs WHERE config_key LIKE 'admin_description_%'"
            ).fetchall()
        self.send_json({
            "values": {row["config_key"].removeprefix("admin_description_"): row["config_value"] for row in rows}
        })

    def admin_update_page_descriptions(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        values = body.get("values", {})
        allowed = {"dashboard", "videos", "watch", "members", "gifts", "invites", "system"}
        if not isinstance(values, dict) or not values:
            raise ApiError("INVALID_PAGE_DESCRIPTION", "没有需要保存的页面说明")
        normalized = {}
        for page, raw in values.items():
            text = str(raw).strip()
            if page not in allowed or not text or len(text) > 160:
                raise ApiError("INVALID_PAGE_DESCRIPTION", "页面说明为1至160个字")
            normalized[page] = text
        with database() as connection:
            for page, text in normalized.items():
                connection.execute(
                    "UPDATE system_configs SET config_value=?, updated_at=? WHERE config_key=?",
                    (text, utc_now(), f"admin_description_{page}"),
                )
            self.write_admin_log(connection, int(staff["id"]), "UPDATE", "PAGE_DESCRIPTION", "admin", normalized)
        self.send_json({"values": normalized})

    def admin_update_configs(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        values = body.get("values", {})
        allowed = {
            "pickup_valid_days": (1, 90), "invite_inviter_points": (0, 100000),
            "invite_invitee_points": (0, 100000),
        }
        if not isinstance(values, dict) or not values:
            raise ApiError("INVALID_CONFIG", "没有可更新的配置")
        normalized = {}
        for key, raw in values.items():
            if key == "customer_service_phone":
                phone = str(raw).strip()
                if phone and not re.fullmatch(r"[0-9-]{5,30}", phone):
                    raise ApiError("INVALID_CONFIG", "客服电话只能填写5至30位数字或短横线")
                normalized[key] = phone
                continue
            if key not in allowed:
                raise ApiError("INVALID_CONFIG", f"不允许修改配置：{key}")
            number = int(raw); low, high = allowed[key]
            if number < low or number > high:
                raise ApiError("INVALID_CONFIG", f"配置 {key} 超出安全范围")
            normalized[key] = str(number)
        with database() as connection:
            for key, value in normalized.items():
                connection.execute(
                    "UPDATE system_configs SET config_value=?, updated_at=? WHERE config_key=?",
                    (value, utc_now(), key),
                )
            self.write_admin_log(connection, int(staff["id"]), "UPDATE", "CONFIG", "business", normalized)
        self.send_json({"values": normalized})

    def admin_list_logs(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT l.*, s.display_name, s.username FROM admin_operation_logs l
                       JOIN staff_users s ON s.id=l.staff_id ORDER BY l.id DESC LIMIT 300"""
                )
            ]
        self.send_json({"items": rows})

    def admin_export_logs(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            rows = connection.execute(
                """SELECT l.created_at, s.username, s.display_name, l.action,
                          l.target_type, l.target_id, l.details
                   FROM admin_operation_logs l
                   JOIN staff_users s ON s.id=l.staff_id ORDER BY l.id DESC"""
            ).fetchall()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["操作时间", "账号", "操作人", "动作", "对象类型", "对象编号", "详情"])
        for row in rows:
            writer.writerow([
                row["created_at"], row["username"], row["display_name"], row["action"],
                row["target_type"], row["target_id"], row["details"],
            ])
        self.send_csv(output.getvalue(), f"operation-logs-{datetime.now().strftime('%Y%m%d')}.csv")

    def admin_list_staff(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT u.id, u.username, u.display_name, u.role, u.store_id, u.status,
                              s.name AS store_name, u.created_at FROM staff_users u
                       LEFT JOIN stores s ON s.id=u.store_id ORDER BY u.id"""
                )
            ]
        self.send_json({"items": rows})

    @staticmethod
    def validate_staff_password(value: str) -> str:
        password = str(value)
        if len(password) < 10 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            raise ApiError("WEAK_PASSWORD", "密码至少10位，并同时包含字母和数字")
        return password

    def admin_change_password(self, body: dict) -> None:
        staff = self.require_staff()
        current_password = str(body.get("currentPassword", ""))
        new_password = self.validate_staff_password(body.get("newPassword", ""))
        with database() as connection:
            existing = connection.execute("SELECT * FROM staff_users WHERE id=?", (staff["id"],)).fetchone()
            if not existing or not hmac.compare_digest(
                password_hash(current_password, existing["password_salt"]), existing["password_hash"]
            ):
                raise ApiError("PASSWORD_INCORRECT", "当前密码不正确", HTTPStatus.FORBIDDEN)
            salt = secrets.token_hex(16)
            connection.execute(
                "UPDATE staff_users SET password_salt=?, password_hash=? WHERE id=?",
                (salt, password_hash(new_password, salt), staff["id"]),
            )
            connection.execute("DELETE FROM staff_sessions WHERE staff_id=?", (staff["id"],))
            self.write_admin_log(connection, int(staff["id"]), "CHANGE_PASSWORD", "STAFF", staff["id"])
        self.send_json({"ok": True})

    def admin_create_staff(self, body: dict) -> None:
        actor = self.require_staff({"ADMIN"})
        username = str(body.get("username", "")).strip()
        display_name = str(body.get("displayName", "")).strip()
        role = str(body.get("role", "STORE_VERIFIER")).upper()
        store_id = int(body.get("storeId", 0)) or None
        password = self.validate_staff_password(body.get("password", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username) or not display_name:
            raise ApiError("INVALID_STAFF", "账号需为3至32位字母、数字或 ._-，姓名不能为空")
        if role not in {"ADMIN", "STORE_VERIFIER"} or (role == "STORE_VERIFIER" and not store_id):
            raise ApiError("INVALID_STAFF", "门店核销员必须选择所属门店")
        with database() as connection:
            if store_id and not connection.execute("SELECT 1 FROM stores WHERE id=?", (store_id,)).fetchone():
                raise ApiError("STORE_NOT_FOUND", "门店不存在", HTTPStatus.NOT_FOUND)
            salt = secrets.token_hex(16)
            try:
                cursor = connection.execute(
                    """INSERT INTO staff_users
                       (username,password_salt,password_hash,display_name,role,store_id)
                       VALUES (?,?,?,?,?,?)""",
                    (username, salt, password_hash(password, salt), display_name, role, store_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError("USERNAME_EXISTS", "后台账号已存在", HTTPStatus.CONFLICT) from exc
            staff_id = int(cursor.lastrowid)
            self.write_admin_log(connection, int(actor["id"]), "CREATE", "STAFF", staff_id, {"username": username, "role": role, "storeId": store_id})
        self.send_json({"id": staff_id}, HTTPStatus.CREATED)

    def admin_update_staff(self, staff_id: int, body: dict) -> None:
        actor = self.require_staff({"ADMIN"})
        with database() as connection:
            existing = connection.execute("SELECT * FROM staff_users WHERE id=?", (staff_id,)).fetchone()
            if not existing:
                raise ApiError("STAFF_NOT_FOUND", "后台账号不存在", HTTPStatus.NOT_FOUND)
            display_name = str(body.get("displayName", existing["display_name"])).strip()
            status = str(body.get("status", existing["status"])).upper()
            role = str(body.get("role", existing["role"])).upper()
            store_id = int(body.get("storeId", existing["store_id"] or 0)) or None
            if not display_name or status not in {"ACTIVE", "DISABLED"} or role not in {"ADMIN", "STORE_VERIFIER"}:
                raise ApiError("INVALID_STAFF", "账号资料或状态不正确")
            if role == "STORE_VERIFIER" and not store_id:
                raise ApiError("INVALID_STAFF", "门店核销员必须选择所属门店")
            if staff_id == int(actor["id"]) and status != "ACTIVE":
                raise ApiError("INVALID_STAFF", "不能停用当前登录账号")
            if store_id and not connection.execute("SELECT 1 FROM stores WHERE id=?", (store_id,)).fetchone():
                raise ApiError("STORE_NOT_FOUND", "门店不存在", HTTPStatus.NOT_FOUND)
            fields = ["display_name=?", "role=?", "store_id=?", "status=?"]
            values = [display_name, role, store_id, status]
            if body.get("password"):
                password = self.validate_staff_password(body["password"])
                salt = secrets.token_hex(16)
                fields.extend(["password_salt=?", "password_hash=?"])
                values.extend([salt, password_hash(password, salt)])
                connection.execute("DELETE FROM staff_sessions WHERE staff_id=?", (staff_id,))
            values.append(staff_id)
            connection.execute(f"UPDATE staff_users SET {', '.join(fields)} WHERE id=?", values)
            self.write_admin_log(connection, int(actor["id"]), "UPDATE", "STAFF", staff_id, {"role": role, "storeId": store_id, "status": status, "passwordReset": bool(body.get("password"))})
        self.send_json({"id": staff_id})

    def admin_list_stores(self) -> None:
        self.require_staff({"ADMIN"})
        with database() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM stores ORDER BY id")]
        self.send_json({"items": rows})

    @staticmethod
    def normalize_store_input(body: dict, existing: sqlite3.Row | None = None) -> dict:
        def pick(api_key: str, db_key: str, default=""):
            return body[api_key] if api_key in body else (existing[db_key] if existing is not None else default)
        item = {
            "name": str(pick("name", "name")).strip(),
            "address": str(pick("address", "address")).strip(),
            "contact_phone": str(pick("contactPhone", "contact_phone")).strip(),
            "business_hours": str(pick("businessHours", "business_hours")).strip(),
            "status": str(pick("status", "status", "ACTIVE")).upper(),
        }
        if not all(item[key] for key in ("name", "address", "contact_phone", "business_hours")):
            raise ApiError("INVALID_STORE", "门店名称、地址、电话和营业时间均不能为空")
        if item["status"] not in {"ACTIVE", "INACTIVE"}:
            raise ApiError("INVALID_STORE", "门店状态不正确")
        return item

    def admin_create_store(self, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        item = self.normalize_store_input(body)
        with database() as connection:
            cursor = connection.execute(
                "INSERT INTO stores (name,address,contact_phone,business_hours,status) VALUES (:name,:address,:contact_phone,:business_hours,:status)", item
            )
            store_id = int(cursor.lastrowid)
            self.write_admin_log(connection, int(staff["id"]), "CREATE", "STORE", store_id, item)
        self.send_json({"id": store_id, **item}, HTTPStatus.CREATED)

    def admin_update_store(self, store_id: int, body: dict) -> None:
        staff = self.require_staff({"ADMIN"})
        with database() as connection:
            existing = connection.execute("SELECT * FROM stores WHERE id=?", (store_id,)).fetchone()
            if not existing:
                raise ApiError("STORE_NOT_FOUND", "门店不存在", HTTPStatus.NOT_FOUND)
            item = self.normalize_store_input(body, existing)
            connection.execute(
                """UPDATE stores SET name=:name,address=:address,contact_phone=:contact_phone,
                   business_hours=:business_hours,status=:status WHERE id=:id""", {**item, "id": store_id}
            )
            self.write_admin_log(connection, int(staff["id"]), "UPDATE", "STORE", store_id, item)
        self.send_json({"id": store_id, **item})

    def create_watch_session(self, body: dict) -> None:
        user_id = self.current_user_id(body)
        video_id = int(body.get("videoId", 0))
        token = str(uuid.uuid4())
        with database() as connection:
            if not connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
                raise ApiError("USER_NOT_FOUND", "用户不存在", HTTPStatus.NOT_FOUND)
            video = connection.execute(
                "SELECT duration_seconds FROM videos WHERE id=? AND status='PUBLISHED'", (video_id,)
            ).fetchone()
            if not video:
                raise ApiError("VIDEO_UNAVAILABLE", "视频不存在", HTTPStatus.NOT_FOUND)
            progress = connection.execute(
                "SELECT valid_seconds FROM watch_progress WHERE user_id=? AND video_id=?",
                (user_id, video_id),
            ).fetchone()
            duration = int(video["duration_seconds"])
            saved_seconds = int(progress["valid_seconds"]) if progress else 0
            resume_position = saved_seconds if 0 < saved_seconds < duration else 0
            connection.execute(
                """INSERT INTO watch_sessions
                   (token, user_id, video_id, last_position_seconds, last_heartbeat_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (token, user_id, video_id, resume_position, time.time()),
            )
        self.send_json({"sessionToken": token, "heartbeatIntervalSeconds": 5, "resumePositionSeconds": resume_position}, HTTPStatus.CREATED)

    def submit_watch_heartbeat(self, body: dict) -> None:
        token = str(body.get("sessionToken", ""))
        request_key = str(body.get("requestId", ""))
        position = float(body.get("positionSeconds", 0))
        if not request_key:
            raise ApiError("REQUEST_ID_REQUIRED", "缺少防重复请求编号")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute(
                "SELECT * FROM watch_sessions WHERE token = ? AND status = 'ACTIVE'", (token,)
            ).fetchone()
            if not session:
                raise ApiError("WATCH_SESSION_INVALID", "观看会话不存在或已结束", HTTPStatus.CONFLICT)
            replay = replay_idempotent(connection, "USER", int(session["user_id"]), "watch-heartbeat", request_key)
            if replay is not None:
                connection.rollback()
                self.send_json(replay)
                return
            if not body.get("playing") or not body.get("pageVisible") or float(body.get("playbackRate", 0)) != 1:
                raise ApiError("WATCH_HEARTBEAT_REJECTED", "暂停、离开页面或倍速播放不计时", HTTPStatus.UNPROCESSABLE_ENTITY)
            elapsed = time.time() - float(session["last_heartbeat_at"])
            position_delta = position - float(session["last_position_seconds"])
            if elapsed < 3.5 or elapsed > 12 or position_delta < 3.5 or position_delta > 6.5:
                raise ApiError("WATCH_HEARTBEAT_REJECTED", "心跳间隔或播放位置异常", HTTPStatus.UNPROCESSABLE_ENTITY)
            tick_no = int(position // 5)
            result = apply_watch_tick(connection, int(session["user_id"]), int(session["video_id"]), tick_no)
            connection.execute(
                "UPDATE watch_sessions SET last_position_seconds = ?, last_heartbeat_at = ?, counted_view = 1 WHERE token = ?",
                (position, time.time(), token),
            )
            save_idempotent(connection, "USER", int(session["user_id"]), "watch-heartbeat", request_key, result)
            connection.commit()
        self.send_json(result)

    def complete_watch_session(self, body: dict) -> None:
        user_id = self.current_user_id(body)
        token = str(body.get("sessionToken", ""))
        request_key = str(body.get("requestId", ""))
        position = float(body.get("positionSeconds", 0))
        if not token or not request_key:
            raise ApiError("INVALID_WATCH_COMPLETION", "缺少观看会话或防重复请求编号")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute(
                "SELECT * FROM watch_sessions WHERE token=? AND user_id=?",
                (token, user_id),
            ).fetchone()
            if not session:
                raise ApiError("WATCH_SESSION_INVALID", "观看会话不存在或已结束", HTTPStatus.CONFLICT)
            replay = replay_idempotent(connection, "USER", user_id, "watch-complete", request_key)
            if replay is not None:
                connection.rollback()
                self.send_json(replay)
                return
            if session["status"] != "ACTIVE":
                raise ApiError("WATCH_SESSION_INVALID", "观看会话不存在或已结束", HTTPStatus.CONFLICT)
            video = connection.execute(
                "SELECT * FROM videos WHERE id=? AND status='PUBLISHED'", (session["video_id"],)
            ).fetchone()
            if not video:
                raise ApiError("VIDEO_UNAVAILABLE", "视频不存在或未上架", HTTPStatus.NOT_FOUND)
            progress = connection.execute(
                "SELECT * FROM watch_progress WHERE user_id=? AND video_id=?",
                (user_id, video["id"]),
            ).fetchone()
            duration = int(video["duration_seconds"])
            valid_seconds = int(progress["valid_seconds"]) if progress else 0
            tail_tolerance = min(10, max(5, duration))
            if (
                position < max(0, duration - 2)
                or float(session["last_position_seconds"]) < max(0, duration - tail_tolerance)
                or valid_seconds < max(0, duration - tail_tolerance)
            ):
                raise ApiError(
                    "WATCH_COMPLETION_REJECTED",
                    "播放记录尚未到达视频结尾，不能结算剩余积分",
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            connection.execute(
                """INSERT OR IGNORE INTO watch_progress
                   (user_id, video_id, valid_seconds, rewarded_points) VALUES (?, ?, 0, 0)""",
                (user_id, video["id"]),
            )
            connection.execute(
                """UPDATE watch_progress SET valid_seconds=?, last_watched_at=?
                   WHERE user_id=? AND video_id=?""",
                (duration, utc_now(), user_id, video["id"]),
            )
            progress = connection.execute(
                "SELECT * FROM watch_progress WHERE user_id=? AND video_id=?",
                (user_id, video["id"]),
            ).fetchone()
            target_points = int(video["total_points"])
            awarded_points = max(0, target_points - int(progress["rewarded_points"]))
            invitation_reward = None
            if awarded_points:
                add_points(
                    connection, user_id, awarded_points, "VIDEO_REWARD",
                    f"watch:{user_id}:{video['id']}:point:{target_points}",
                    f"完整观看《{video['title']}》（本次+{awarded_points}分，累计{target_points}/{video['total_points']}分）",
                )
                connection.execute(
                    "UPDATE watch_progress SET rewarded_points=? WHERE user_id=? AND video_id=?",
                    (target_points, user_id, video["id"]),
                )
                invitation_reward = maybe_reward_invitation(connection, user_id)
            connection.execute("UPDATE watch_sessions SET status='CLOSED' WHERE token=?", (token,))
            balance = int(connection.execute("SELECT points FROM users WHERE id=?", (user_id,)).fetchone()[0])
            result = {
                "acceptedSeconds": max(0, duration - valid_seconds), "validSeconds": duration,
                "completed": True, "awardedPoints": awarded_points, "balance": balance,
                "invitationReward": invitation_reward,
            }
            save_idempotent(connection, "USER", user_id, "watch-complete", request_key, result)
            connection.commit()
        self.send_json(result)

    def dev_watch_step(self, body: dict) -> None:
        user_id = self.current_user_id(body)
        video_id = int(body.get("videoId", 0))
        request_key = str(body.get("requestId", ""))
        if not request_key:
            raise ApiError("REQUEST_ID_REQUIRED", "缺少防重复请求编号")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = replay_idempotent(connection, "USER", user_id, "dev-watch-step", request_key)
            if replay is not None:
                connection.rollback()
                self.send_json(replay)
                return
            tick_row = connection.execute(
                "SELECT COALESCE(MAX(tick_no), 0) + 1 FROM watch_ticks WHERE user_id = ? AND video_id = ?",
                (user_id, video_id),
            ).fetchone()
            result = apply_watch_tick(connection, user_id, video_id, int(tick_row[0]))
            save_idempotent(connection, "USER", user_id, "dev-watch-step", request_key, result)
            connection.commit()
        self.send_json(result)

    def create_pickup(self, body: dict) -> None:
        user_id = self.current_user_id(body)
        gift_id = int(body.get("giftId", 0))
        store_id = int(body.get("storeId", 0))
        request_key = str(body.get("requestId", ""))
        contact_name = str(body.get("contactName", "")).strip()
        contact_mobile = str(body.get("contactMobile", "")).strip()
        if not request_key or not contact_name or len(contact_mobile) < 6:
            raise ApiError("INVALID_PICKUP_INPUT", "请填写联系人、手机号和请求编号")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = replay_idempotent(connection, "USER", user_id, "create-pickup", request_key)
            if replay is not None:
                connection.rollback()
                self.send_json(replay, HTTPStatus.CREATED)
                return
            gift = connection.execute("SELECT * FROM gifts WHERE id = ? AND status = 'ACTIVE'", (gift_id,)).fetchone()
            inventory = connection.execute(
                "SELECT * FROM store_gift_inventory WHERE store_id = ? AND gift_id = ?",
                (store_id, gift_id),
            ).fetchone()
            if not gift:
                raise ApiError("GIFT_UNAVAILABLE", "礼品不存在或已下架", HTTPStatus.NOT_FOUND)
            if not inventory or int(inventory["available_quantity"]) <= 0:
                raise ApiError("INVENTORY_NOT_AVAILABLE", "所选门店暂无库存", HTTPStatus.CONFLICT)
            request_no = f"PU{datetime.now().strftime('%m%d%H%M%S')}{secrets.randbelow(1000):03d}"
            voucher = secrets.token_hex(8).upper()
            add_points(
                connection,
                user_id,
                -int(gift["points_cost"]),
                "PICKUP_DEBIT",
                f"pickup:{request_no}:debit",
                f"申请到店领取：{gift['name']}",
            )
            connection.execute(
                """UPDATE store_gift_inventory
                   SET available_quantity = available_quantity - 1,
                       locked_quantity = locked_quantity + 1
                   WHERE store_id = ? AND gift_id = ?""",
                (store_id, gift_id),
            )
            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(days=config_int(connection, "pickup_valid_days", 7))
            ).isoformat(timespec="seconds")
            cursor = connection.execute(
                """INSERT INTO pickup_requests
                   (request_no, user_id, gift_id, store_id, contact_name, contact_mobile,
                    points_cost, voucher_code, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_no,
                    user_id,
                    gift_id,
                    store_id,
                    contact_name,
                    contact_mobile,
                    int(gift["points_cost"]),
                    voucher,
                    expires_at,
                ),
            )
            pickup_id = int(cursor.lastrowid)
            connection.execute(
                """INSERT INTO pickup_status_logs
                   (pickup_id, from_status, to_status, operator_type, remark)
                   VALUES (?, NULL, 'WAIT_CONTACT', 'USER', '用户提交到店领取申请')""",
                (pickup_id,),
            )
            connection.execute(
                "INSERT INTO store_leads (pickup_id, store_id, user_id) VALUES (?, ?, ?)",
                (pickup_id, store_id, user_id),
            )
            response = {
                "id": pickup_id,
                "requestNo": request_no,
                "voucherCode": voucher,
                "status": "WAIT_CONTACT",
                "expiresAt": expires_at,
            }
            save_idempotent(connection, "USER", user_id, "create-pickup", request_key, response)
            connection.commit()
        self.send_json(response, HTTPStatus.CREATED)

    def create_pickup_voucher_qr(self, pickup_id: int, body: dict) -> None:
        user_id = self.current_user_id(body)
        with database() as connection:
            pickup = connection.execute(
                """SELECT p.id, p.request_no, p.voucher_code, p.status, g.name AS gift_name,
                          s.name AS store_name
                   FROM pickup_requests p JOIN gifts g ON g.id=p.gift_id
                   JOIN stores s ON s.id=p.store_id
                   WHERE p.id=? AND p.user_id=?""",
                (pickup_id, user_id),
            ).fetchone()
        if not pickup:
            raise ApiError("PICKUP_NOT_FOUND", "领取申请不存在", HTTPStatus.NOT_FOUND)
        qr_text = f"到店领取码：{pickup['voucher_code']}"
        image = qrcode.make(qr_text)
        output = io.BytesIO()
        image.save(output, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        self.send_json({
            "dataUrl": data_url,
            "voucherCode": pickup["voucher_code"],
            "requestNo": pickup["request_no"],
            "giftName": pickup["gift_name"],
            "storeName": pickup["store_name"],
            "status": pickup["status"],
        })

    def cancel_pickup(self, pickup_id: int, body: dict) -> None:
        user_id = self.current_user_id(body)
        request_key = str(body.get("requestId", ""))
        if not request_key:
            raise ApiError("REQUEST_ID_REQUIRED", "缺少防重复请求编号")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = replay_idempotent(connection, "USER", user_id, "cancel-pickup", request_key)
            if replay is not None:
                connection.rollback()
                self.send_json(replay)
                return
            pickup = connection.execute(
                "SELECT * FROM pickup_requests WHERE id = ? AND user_id = ?", (pickup_id, user_id)
            ).fetchone()
            if not pickup:
                raise ApiError("PICKUP_NOT_FOUND", "领取申请不存在", HTTPStatus.NOT_FOUND)
            if pickup["status"] not in {"WAIT_CONTACT", "WAIT_ARRIVAL"}:
                raise ApiError("PICKUP_STATE_CONFLICT", "当前状态不能取消", HTTPStatus.CONFLICT)
            add_points(
                connection,
                user_id,
                int(pickup["points_cost"]),
                "PICKUP_REFUND",
                f"pickup:{pickup['request_no']}:refund",
                "取消到店领取，退回积分",
            )
            connection.execute(
                """UPDATE store_gift_inventory
                   SET available_quantity = available_quantity + 1,
                       locked_quantity = locked_quantity - 1
                   WHERE store_id = ? AND gift_id = ?""",
                (pickup["store_id"], pickup["gift_id"]),
            )
            connection.execute(
                "UPDATE pickup_requests SET status = 'CANCELLED', updated_at = ? WHERE id = ?",
                (utc_now(), pickup_id),
            )
            connection.execute(
                """INSERT INTO pickup_status_logs
                   (pickup_id, from_status, to_status, operator_type, remark)
                   VALUES (?, ?, 'CANCELLED', 'USER', ?)""",
                (pickup_id, pickup["status"], str(body.get("reason", "用户取消"))),
            )
            response = {"id": pickup_id, "status": "CANCELLED", "refundedPoints": int(pickup["points_cost"])}
            save_idempotent(connection, "USER", user_id, "cancel-pickup", request_key, response)
            connection.commit()
        self.send_json(response)

    def staff_pickup_action(self, pickup_id: int, action: str, body: dict, staff: dict) -> None:
        if staff["role"] == "STORE_VERIFIER":
            staff_store_id = int(staff["store_id"] or 0)
        else:
            staff_store_id = int(body.get("staffStoreId", 1))
        request_key = str(body.get("requestId", ""))
        if not request_key:
            raise ApiError("REQUEST_ID_REQUIRED", "缺少防重复请求编号")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = replay_idempotent(connection, "STAFF", int(staff["id"]), f"pickup-{action}", request_key)
            if replay is not None:
                connection.rollback()
                self.send_json(replay)
                return
            pickup = connection.execute("SELECT * FROM pickup_requests WHERE id = ?", (pickup_id,)).fetchone()
            if not pickup:
                raise ApiError("PICKUP_NOT_FOUND", "领取申请不存在", HTTPStatus.NOT_FOUND)
            if int(pickup["store_id"]) != staff_store_id:
                raise ApiError("FORBIDDEN", "门店人员只能处理本店申请", HTTPStatus.FORBIDDEN)

            if action == "contacted":
                if pickup["status"] != "WAIT_CONTACT":
                    raise ApiError("PICKUP_STATE_CONFLICT", "当前状态不能标记联系", HTTPStatus.CONFLICT)
                new_status = "WAIT_ARRIVAL"
                lead_status = "CONTACTED"
            elif action == "verify":
                if pickup["status"] != "WAIT_ARRIVAL":
                    raise ApiError("PICKUP_STATE_CONFLICT", "只有待到店申请可以核销", HTTPStatus.CONFLICT)
                if str(body.get("voucherCode", "")) != pickup["voucher_code"]:
                    raise ApiError("VOUCHER_INVALID", "领取凭证不正确", HTTPStatus.CONFLICT)
                new_status = "PICKED_UP"
                lead_status = "ARRIVED"
                connection.execute(
                    """UPDATE store_gift_inventory
                       SET locked_quantity = locked_quantity - 1,
                           redeemed_quantity = redeemed_quantity + 1
                       WHERE store_id = ? AND gift_id = ?""",
                    (pickup["store_id"], pickup["gift_id"]),
                )
            else:
                if pickup["status"] not in {"WAIT_CONTACT", "WAIT_ARRIVAL"}:
                    raise ApiError("PICKUP_STATE_CONFLICT", "当前状态不能拒绝", HTTPStatus.CONFLICT)
                new_status = "REJECTED"
                lead_status = "CLOSED"
                add_points(
                    connection,
                    int(pickup["user_id"]),
                    int(pickup["points_cost"]),
                    "PICKUP_REFUND",
                    f"pickup:{pickup['request_no']}:refund",
                    "门店拒绝申请，退回积分",
                )
                connection.execute(
                    """UPDATE store_gift_inventory
                       SET available_quantity = available_quantity + 1,
                           locked_quantity = locked_quantity - 1
                       WHERE store_id = ? AND gift_id = ?""",
                    (pickup["store_id"], pickup["gift_id"]),
                )

            connection.execute(
                "UPDATE pickup_requests SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, utc_now(), pickup_id),
            )
            connection.execute("UPDATE store_leads SET status = ? WHERE pickup_id = ?", (lead_status, pickup_id))
            connection.execute(
                """INSERT INTO pickup_status_logs
                   (pickup_id, from_status, to_status, operator_type, remark)
                   VALUES (?, ?, ?, 'STAFF', ?)""",
                (pickup_id, pickup["status"], new_status, str(body.get("remark", "门店处理"))),
            )
            self.write_admin_log(
                connection, int(staff["id"]), f"PICKUP_{action.upper()}", "PICKUP", pickup_id,
                {"from": pickup["status"], "to": new_status, "storeId": staff_store_id},
            )
            response = {"id": pickup_id, "status": new_status}
            save_idempotent(connection, "STAFF", int(staff["id"]), f"pickup-{action}", request_key, response)
            connection.commit()
        self.send_json(response)

    def create_invitee(self, body: dict) -> None:
        if not DEV_ENDPOINTS_ENABLED:
            raise ApiError("NOT_FOUND", "接口不存在", HTTPStatus.NOT_FOUND)
        inviter_id = self.current_user_id({"userId": body.get("inviterId", 0)})
        name = str(body.get("name", "李叔叔（受邀用户）")).strip() or "李叔叔（受邀用户）"
        request_key = str(body.get("requestId", ""))
        if not request_key:
            raise ApiError("REQUEST_ID_REQUIRED", "缺少防重复请求编号")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = replay_idempotent(connection, "USER", inviter_id, "create-invitee", request_key)
            if replay is not None:
                connection.rollback()
                self.send_json(replay, HTTPStatus.CREATED)
                return
            inviter = connection.execute("SELECT * FROM users WHERE id = ?", (inviter_id,)).fetchone()
            if not inviter:
                raise ApiError("USER_NOT_FOUND", "邀请人不存在", HTTPStatus.NOT_FOUND)
            cursor = connection.execute(
                "INSERT INTO users (name, invite_code, points) VALUES (?, ?, 0)",
                (name, secrets.token_hex(4).upper()),
            )
            invitee_id = int(cursor.lastrowid)
            invitation_cursor = connection.execute(
                "INSERT INTO invitations (inviter_user_id, invitee_user_id) VALUES (?, ?)",
                (inviter_id, invitee_id),
            )
            response = {
                "invitationId": int(invitation_cursor.lastrowid),
                "inviteeUserId": invitee_id,
                "status": "BOUND",
                "inviterCode": inviter["invite_code"],
            }
            save_idempotent(connection, "USER", inviter_id, "create-invitee", request_key, response)
            connection.commit()
        self.send_json(response, HTTPStatus.CREATED)

    def create_invitation_mini_code(self) -> None:
        user = self.require_user()
        app_id = os.environ.get("WX_APPID", "").strip()
        app_secret = os.environ.get("WX_APPSECRET", "").strip()
        if not app_id or not app_secret:
            raise ApiError(
                "WECHAT_MINI_CODE_NOT_CONFIGURED",
                "正式AppID和AppSecret尚未配置，当前可先使用微信分享入口",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        environment = os.environ.get("WX_MINIPROGRAM_ENV", "release").strip().lower()
        if environment not in {"release", "trial", "develop"}:
            environment = "release"
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        filename = f"invite-code-{int(user['id'])}-{environment}.png"
        output_path = UPLOAD_ROOT / filename
        if output_path.exists() and output_path.stat().st_size > 100:
            self.send_json({"url": f"/uploads/{filename}", "sourceType": "QR_CODE"})
            return
        token_query = urlencode(
            {"grant_type": "client_credential", "appid": app_id, "secret": app_secret}
        )
        try:
            with urlopen(f"https://api.weixin.qq.com/cgi-bin/token?{token_query}", timeout=8) as response:
                token_result = json.loads(response.read().decode("utf-8"))
            access_token = str(token_result.get("access_token", ""))
            if not access_token:
                raise ValueError(token_result.get("errmsg", "access token unavailable"))
            payload = json.dumps(
                {
                    "scene": f"i={user['invite_code']}",
                    "page": "pages/home/home",
                    "env_version": environment,
                    "width": 430,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            request = Request(
                f"https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token={access_token}",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=15) as response:
                image_bytes = response.read()
            if image_bytes.startswith(b"{"):
                error_result = json.loads(image_bytes.decode("utf-8"))
                raise ValueError(error_result.get("errmsg", "mini code unavailable"))
            if len(image_bytes) < 100:
                raise ValueError("mini code image is empty")
        except Exception as exc:
            print(f"Wechat mini code error: {exc!r}")
            raise ApiError(
                "WECHAT_MINI_CODE_FAILED",
                "小程序码暂时无法生成，请使用微信分享入口或稍后重试",
                HTTPStatus.BAD_GATEWAY,
            ) from exc
        output_path.write_bytes(image_bytes)
        self.send_json({"url": f"/uploads/{filename}", "sourceType": "QR_CODE"}, HTTPStatus.CREATED)

    def serve_static(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html", "/app.js", "/styles.css"}:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/admin")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if parsed.path == "/admin" or parsed.path.startswith("/admin/"):
            static_root = ADMIN_ROOT
            relative = parsed.path.removeprefix("/admin").lstrip("/") or "index.html"
        elif parsed.path.startswith(("/assets/", "/media/", "/uploads/")):
            static_root = WEB_ROOT
            relative = parsed.path.lstrip("/") or "index.html"
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        candidate = (static_root / relative).resolve()
        if static_root.resolve() not in candidate.parents and candidate != static_root.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'",
        )
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        try:
            if self.path.startswith("/api/"):
                self.handle_api()
            else:
                self.serve_static()
        except ApiError as error:
            self.send_json({"code": error.code, "message": error.message}, error.status)
        except Exception as error:
            print(f"Unhandled error: {error!r}")
            self.send_json({"code": "INTERNAL_ERROR", "message": "服务器内部错误"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        try:
            self.handle_api()
        except ApiError as error:
            self.send_json({"code": error.code, "message": error.message}, error.status)
        except sqlite3.IntegrityError as error:
            print(f"Integrity error: {error!r}")
            self.send_json({"code": "DATA_CONFLICT", "message": "数据发生冲突，请刷新后重试"}, HTTPStatus.CONFLICT)
        except Exception as error:
            print(f"Unhandled error: {error!r}")
            self.send_json({"code": "INTERNAL_ERROR", "message": "服务器内部错误"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self) -> None:
        self.do_POST()

    def do_PUT(self) -> None:
        self.do_POST()

    def do_DELETE(self) -> None:
        self.do_POST()


class AppHTTPServer(ThreadingHTTPServer):
    # ThreadingHTTPServer 默认只允许很短的 TCP 等候队列，突发连接会在进入业务
    # 逻辑前被系统重置。128 足以覆盖首版 50 人同时观看验收，线程仍受容器限制。
    request_queue_size = 128
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local mini-program business system")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    initialize_database(reset=args.reset)
    server = AppHTTPServer((args.host, args.port), AppHandler)
    print(f"Mini-program: http://{args.host}:{args.port}")
    print(f"Admin console: http://{args.host}:{args.port}/admin")
    print(f"Database: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
