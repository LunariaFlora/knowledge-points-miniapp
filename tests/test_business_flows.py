from __future__ import annotations

import json
import base64
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ENABLE_DEV_ENDPOINTS"] = "1"

from app.server import (  # noqa: E402
    AppHandler,
    ThreadingHTTPServer,
    bind_invitation_by_code,
    database,
    initialize_database,
)
import app.server as server_module  # noqa: E402


class BusinessFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        initialize_database(reset=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def snapshot(self, user_id: int) -> dict:
        status, data = self.request("GET", f"/api/snapshot?userId={user_id}")
        self.assertEqual(status, 200)
        return data

    def request_bytes(self, path: str, headers: dict | None = None) -> tuple[int, bytes, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method="GET", headers=headers or {},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(), dict(response.headers)

    def admin_auth(self) -> dict:
        status, login = self.request(
            "POST", "/api/admin/login", {"username": "admin", "password": "dev-only-password"}
        )
        self.assertEqual(status, 200)
        return {"Authorization": f"Bearer {login['token']}"}

    def watch_video_one(self, user_id: int, prefix: str) -> dict:
        result = {}
        for index in range(9):
            status, result = self.request(
                "POST",
                "/api/dev/watch-step",
                {"userId": user_id, "videoId": 1, "requestId": f"{prefix}-{index}"},
            )
            self.assertEqual(status, 200)
        return result

    def test_invite_code_binds_only_once_and_prevents_self_invite(self) -> None:
        status, _ = self.request("POST", "/api/dev/reset", {})
        self.assertEqual(status, 200)
        with database() as connection:
            invitee_id = int(
                connection.execute(
                    "INSERT INTO users (name, invite_code, points) VALUES (?, ?, 0)",
                    ("分享落地新用户", "NEWUSER1"),
                ).lastrowid
            )
            self.assertTrue(bind_invitation_by_code(
                connection, invitee_id, "WANG2026", "QR_CODE", "i=WANG2026", "1047"
            ))
            self.assertFalse(bind_invitation_by_code(connection, invitee_id, "WANG2026"))
            self.assertFalse(bind_invitation_by_code(connection, invitee_id, "NEWUSER1"))
            invitation = connection.execute(
                """SELECT inviter_user_id, invitee_user_id, source_type, source_value, landing_scene
                   FROM invitations WHERE invitee_user_id=?""",
                (invitee_id,),
            ).fetchone()
            self.assertEqual(int(invitation["inviter_user_id"]), 1)
            self.assertEqual(invitation["source_type"], "QR_CODE")
            self.assertEqual(invitation["source_value"], "i=WANG2026")
            self.assertEqual(invitation["landing_scene"], "1047")
            # A 已邀请 B 后，B 不能反向邀请 A，否则会形成循环关系。
            self.assertFalse(bind_invitation_by_code(connection, 1, "NEWUSER1", "SHARE_CARD"))
            third_user_id = int(
                connection.execute(
                    "INSERT INTO users (name, invite_code, points) VALUES (?, ?, 0)",
                    ("邀请链第三人", "NEWUSER2"),
                ).lastrowid
            )
            # B 邀请 C 合法；A 与 C 只是间接上下级，不新增 A→C 的直接邀请记录。
            self.assertTrue(bind_invitation_by_code(connection, third_user_id, "NEWUSER1", "SHARE_CARD"))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM invitations WHERE inviter_user_id=1 AND invitee_user_id=?",
                    (third_user_id,),
                ).fetchone()[0],
                0,
            )

    def test_watch_completion_settles_tail_and_replays_safely(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        status, login = self.request("POST", "/api/auth/dev-login", {"userId": 1})
        self.assertEqual(status, 200)
        auth = {"Authorization": f"Bearer {login['token']}"}
        status, session = self.request("POST", "/api/watch/sessions", {"videoId": 1}, auth)
        self.assertEqual(status, 201)
        with database() as connection:
            connection.execute(
                """INSERT INTO watch_progress (user_id, video_id, valid_seconds, rewarded_points)
                   VALUES (1, 1, 40, 0)"""
            )
            connection.execute(
                "UPDATE watch_sessions SET last_position_seconds=40 WHERE token=?",
                (session["sessionToken"],),
            )
        payload = {
            "sessionToken": session["sessionToken"], "requestId": "complete-tail-once",
            "positionSeconds": 45,
        }
        status, completed = self.request("POST", "/api/watch/complete", payload, auth)
        self.assertEqual(status, 200)
        self.assertTrue(completed["completed"])
        self.assertEqual(completed["awardedPoints"], 1)
        status, replay = self.request("POST", "/api/watch/complete", payload, auth)
        self.assertEqual(status, 200)
        self.assertEqual(replay, completed)
        self.assertEqual(self.snapshot(1)["user"]["points"], 6)

    def test_watch_session_resumes_and_counts_real_plays(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        status, login = self.request("POST", "/api/auth/dev-login", {"userId": 1})
        self.assertEqual(status, 200)
        auth = {"Authorization": f"Bearer {login['token']}"}
        with database() as connection:
            connection.execute(
                """INSERT INTO watch_progress (user_id, video_id, valid_seconds, rewarded_points)
                   VALUES (1, 1, 10, 0)"""
            )
        status, session = self.request("POST", "/api/watch/sessions", {"videoId": 1}, auth)
        self.assertEqual(status, 201)
        self.assertEqual(session["resumePositionSeconds"], 10)
        with database() as connection:
            connection.execute(
                "UPDATE watch_sessions SET last_heartbeat_at=last_heartbeat_at-5 WHERE token=?",
                (session["sessionToken"],),
            )
        status, heartbeat = self.request(
            "POST", "/api/watch/heartbeat",
            {"sessionToken": session["sessionToken"], "requestId": "resume-heartbeat-15",
             "positionSeconds": 15, "playing": True, "pageVisible": True, "playbackRate": 1}, auth,
        )
        self.assertEqual(status, 200)
        self.assertEqual(heartbeat["validSeconds"], 15)
        video = self.snapshot(1)["videos"][0]
        self.assertEqual(video["user_valid_seconds"], 15)
        self.assertEqual(video["view_count"], 1)

    def test_official_invitation_code_requires_server_credentials(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        status, login = self.request("POST", "/api/auth/dev-login", {"userId": 1})
        self.assertEqual(status, 200)
        old_app_id, old_secret = os.environ.pop("WX_APPID", None), os.environ.pop("WX_APPSECRET", None)
        try:
            status, error = self.request(
                "POST", "/api/invitations/mini-code", {},
                {"Authorization": f"Bearer {login['token']}"},
            )
        finally:
            if old_app_id is not None: os.environ["WX_APPID"] = old_app_id
            if old_secret is not None: os.environ["WX_APPSECRET"] = old_secret
        self.assertEqual(status, 503)
        self.assertEqual(error["code"], "WECHAT_MINI_CODE_NOT_CONFIGURED")

    def test_three_business_loops(self) -> None:
        status, _ = self.request("POST", "/api/dev/reset", {})
        self.assertEqual(status, 200)
        initial_state = self.snapshot(1)
        staff_auth = self.admin_auth()
        self.assertEqual(len(initial_state["videos"]), 4)
        self.assertEqual(len(initial_state["gifts"]), 4)

        # A. 45秒视频按四舍五入总计1分，播放结束时补足尾段积分；重复请求不重复变化。
        watch_result = self.watch_video_one(1, "main-watch")
        self.assertEqual(watch_result["validSeconds"], 45)
        self.assertEqual(watch_result["awardedPoints"], 1)
        self.assertEqual(watch_result["balance"], 6)
        status, replay = self.request(
            "POST",
            "/api/dev/watch-step",
            {"userId": 1, "videoId": 1, "requestId": "main-watch-8"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(replay, watch_result)
        self.assertEqual(self.snapshot(1)["user"]["points"], 6)

        # B1. 申请到店领取后扣积分、锁库存，门店联系并核销。
        pickup_payload = {
            "userId": 1,
            "giftId": 1,
            "storeId": 1,
            "contactName": "王阿姨",
            "contactMobile": "13800000001",
            "requestId": "pickup-create-1",
        }
        status, pickup = self.request("POST", "/api/pickups", pickup_payload)
        self.assertEqual(status, 201)
        status, pickup_replay = self.request("POST", "/api/pickups", pickup_payload)
        self.assertEqual(status, 201)
        self.assertEqual(pickup_replay["id"], pickup["id"])
        state = self.snapshot(1)
        self.assertEqual(state["user"]["points"], 3)
        self.assertEqual(state["inventory"]["available_quantity"], 7)
        self.assertEqual(state["inventory"]["locked_quantity"], 1)
        status, voucher_qr = self.request(
            "POST", f"/api/pickups/{pickup['id']}/voucher-qr", {"userId": 1}
        )
        self.assertEqual(status, 200)
        self.assertEqual(voucher_qr["voucherCode"], pickup["voucherCode"])
        self.assertTrue(voucher_qr["dataUrl"].startswith("data:image/png;base64,"))
        self.assertEqual(self.snapshot(1)["pickups"][0]["status"], "WAIT_CONTACT")

        # 二维码只能由申请本人获取，而且生成二维码本身不能把领取单提前核销。
        status, error = self.request(
            "POST", f"/api/pickups/{pickup['id']}/voucher-qr", {"userId": 2}
        )
        self.assertEqual(status, 404)
        self.assertEqual(error["code"], "PICKUP_NOT_FOUND")

        status, contacted = self.request(
            "POST",
            f"/api/staff/pickups/{pickup['id']}/contacted",
            {"staffStoreId": 1, "requestId": "contact-1"},
            staff_auth,
        )
        self.assertEqual(status, 200)
        self.assertEqual(contacted["status"], "WAIT_ARRIVAL")
        status, verified = self.request(
            "POST",
            f"/api/staff/pickups/{pickup['id']}/verify",
            {
                "staffStoreId": 1,
                "voucherCode": pickup["voucherCode"],
                "requestId": "verify-1",
            },
            staff_auth,
        )
        self.assertEqual(status, 200)
        self.assertEqual(verified["status"], "PICKED_UP")
        state = self.snapshot(1)
        self.assertEqual(state["inventory"]["locked_quantity"], 0)
        self.assertEqual(state["inventory"]["redeemed_quantity"], 1)

        # B2. 取消另一条申请后，积分和库存均恢复，并保留退款流水。
        second_payload = dict(pickup_payload, requestId="pickup-create-2")
        status, second_pickup = self.request("POST", "/api/pickups", second_payload)
        self.assertEqual(status, 201)
        balance_after_debit = self.snapshot(1)["user"]["points"]
        status, cancelled = self.request(
            "POST",
            f"/api/pickups/{second_pickup['id']}/cancel",
            {"userId": 1, "requestId": "pickup-cancel-2", "reason": "验收取消"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(self.snapshot(1)["user"]["points"], balance_after_debit + 3)

        # C. 新用户绑定邀请人，首次获得观看积分后双方只奖励一次。
        status, invitation = self.request(
            "POST",
            "/api/invitations/create-invitee",
            {"inviterId": 1, "name": "李叔叔（受邀用户）", "requestId": "invite-create-1"},
        )
        self.assertEqual(status, 201)
        invitee_id = invitation["inviteeUserId"]
        invited_watch = self.watch_video_one(invitee_id, "invitee-watch")
        self.assertIsNotNone(invited_watch["invitationReward"])
        self.assertEqual(self.snapshot(invitee_id)["user"]["points"], 11)
        inviter_state = self.snapshot(1)
        self.assertEqual(inviter_state["user"]["points"], 53)
        status, invite_admin = self.request("GET", "/api/admin/invitations", headers=staff_auth)
        self.assertEqual(status, 200)
        self.assertEqual(invite_admin["summary"]["rewarded"], 1)

        # 额外的5秒不会重复触发邀请奖。
        status, _ = self.request(
            "POST",
            "/api/dev/watch-step",
            {"userId": invitee_id, "videoId": 1, "requestId": "invitee-extra"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.snapshot(1)["user"]["points"], 53)

        # 核销员不能处理其他门店数据。
        third_payload = dict(pickup_payload, requestId="pickup-create-3")
        status, third_pickup = self.request("POST", "/api/pickups", third_payload)
        self.assertEqual(status, 201)
        status, error = self.request(
            "POST",
            f"/api/staff/pickups/{third_pickup['id']}/contacted",
            {"staffStoreId": 2, "requestId": "wrong-store"},
            staff_auth,
        )
        self.assertEqual(status, 403)
        self.assertEqual(error["code"], "FORBIDDEN")

    def test_admin_ui_and_legacy_browser_user_retirement(self) -> None:
        admin_html = (ROOT / "admin" / "index.html").read_text(encoding="utf-8")
        admin_responsive = (ROOT / "admin" / "responsive.css").read_text(encoding="utf-8")
        for admin_label in ["运营概览", "知识视频", "观看数据", "礼品与领取记录", "会员管理", "邀请关系", "导出日志 CSV"]:
            self.assertIn(admin_label, admin_html)
        self.assertNotIn('id="log-list"', admin_html)
        self.assertNotIn("查看用户端", admin_html)
        self.assertIn("上传视频文件", admin_html)
        self.assertIn("选择文件后自动生成", admin_html)
        self.assertIn("/admin/calm-console.css", admin_html)
        self.assertNotIn("<th>视频来源</th>", admin_html)
        self.assertIn('name="durationSeconds" type="number" min="1" value="60" readonly', admin_html)
        self.assertIn('name="totalPoints"', admin_html)
        self.assertIn('name="tags"', admin_html)
        self.assertIn("data-video-action=\"delete\"", (ROOT / "admin" / "app.js").read_text(encoding="utf-8"))
        self.assertIn(".app-shell nav", admin_responsive)

        status, root_content, _ = self.request_bytes("/")
        self.assertEqual(status, 200)
        root_html = root_content.decode("utf-8")
        self.assertIn("Health Knowledge Points Console", root_html)
        self.assertNotIn("点击观看视频", root_html)

    def test_production_compose_does_not_seed_fixed_verifier_password(self) -> None:
        compose = (ROOT / "deploy" / "compose.server-test.yaml").read_text(encoding="utf-8")
        self.assertIn('LOCAL_ADMIN_PASSWORD: "${LOCAL_ADMIN_PASSWORD:-}"', compose)
        self.assertIn('SEED_DEFAULT_VERIFIER: "0"', compose)
        self.assertNotIn("Verifier123!", compose)

        with tempfile.TemporaryDirectory(prefix="production-seed-") as temp_dir:
            temp_database = Path(temp_dir) / "fresh.sqlite3"
            with (
                patch.object(server_module, "DB_PATH", temp_database),
                patch.object(server_module, "DEV_ENDPOINTS_ENABLED", False),
                patch.dict(os.environ, {"LOCAL_ADMIN_PASSWORD": "", "SEED_DEFAULT_VERIFIER": "0"}),
            ):
                with self.assertRaisesRegex(RuntimeError, "LOCAL_ADMIN_PASSWORD"):
                    server_module.initialize_database(reset=True)

                os.environ["LOCAL_ADMIN_PASSWORD"] = "FreshInstall567!"
                server_module.initialize_database(reset=True)
                connection = sqlite3.connect(temp_database)
                try:
                    accounts = connection.execute(
                        "SELECT username, role FROM staff_users ORDER BY id"
                    ).fetchall()
                finally:
                    connection.close()
                self.assertEqual(accounts, [("admin", "ADMIN")])

    def test_admin_publishes_video_to_user_app(self) -> None:
        status, _ = self.request("POST", "/api/dev/reset", {})
        self.assertEqual(status, 200)

        status, login = self.request(
            "POST", "/api/admin/login", {"username": "admin", "password": "dev-only-password"}
        )
        self.assertEqual(status, 200)
        auth = {"Authorization": f"Bearer {login['token']}"}

        status, result = self.request("GET", "/api/admin/videos", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(len(result["items"]), 4)

        status, video = self.request(
            "POST",
            "/api/admin/videos",
            {
                "title": "后台新发布的知识课",
                "summary": "用于验证后台与用户端共享同一份数据。",
                "coverUrl": "/assets/video-conference.svg",
                "mediaUrl": "/media/knowledge-demo.mp4",
                "category": "后台联调",
                "tags": "肺结节,循证医学",
                "durationSeconds": 3600,
                "totalPoints": 7,
                "status": "PUBLISHED",
            },
            auth,
        )
        self.assertEqual(status, 201)
        published = next(item for item in self.snapshot(1)["videos"] if item["title"] == "后台新发布的知识课")
        self.assertEqual(published["tags"], "肺结节,循证医学")
        self.assertEqual(published["total_points"], 7)

        status, _ = self.request(
            "PATCH", f"/api/admin/videos/{video['id']}", {"status": "OFFLINE"}, auth
        )
        self.assertEqual(status, 200)
        self.assertNotIn("后台新发布的知识课", [item["title"] for item in self.snapshot(1)["videos"]])

        status, error = self.request("GET", "/api/admin/videos")
        self.assertEqual(status, 401)
        self.assertEqual(error["code"], "UNAUTHORIZED")

    def test_video_points_arrive_each_minute_and_stop_at_rounded_total(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        auth = self.admin_auth()
        status, video = self.request(
            "POST", "/api/admin/videos",
            {
                "title": "95秒渐进计分测试", "summary": "每分钟逐步到账，结尾补齐总分。",
                "coverUrl": "/assets/video-conference.svg",
                "mediaUrl": "/media/knowledge-demo.mp4", "category": "规则验收",
                "durationSeconds": 95, "status": "PUBLISHED",
            }, auth,
        )
        self.assertEqual(status, 201)
        self.assertEqual(video["total_points"], 2)

        latest = None
        for index in range(11):
            status, latest = self.request(
                "POST", "/api/dev/watch-step",
                {"userId": 1, "videoId": video["id"], "requestId": f"progressive-{index}"},
            )
            self.assertEqual(status, 200)
        self.assertEqual(latest["validSeconds"], 55)
        self.assertEqual(latest["awardedPoints"], 0)

        status, minute = self.request(
            "POST", "/api/dev/watch-step",
            {"userId": 1, "videoId": video["id"], "requestId": "progressive-11"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(minute["validSeconds"], 60)
        self.assertEqual(minute["awardedPoints"], 1)

        for index in range(12, 19):
            status, latest = self.request(
                "POST", "/api/dev/watch-step",
                {"userId": 1, "videoId": video["id"], "requestId": f"progressive-{index}"},
            )
            self.assertEqual(status, 200)
        self.assertEqual(latest["validSeconds"], 95)
        self.assertEqual(latest["awardedPoints"], 1)
        self.assertEqual(self.snapshot(1)["user"]["points"], 7)

        status, extra = self.request(
            "POST", "/api/dev/watch-step",
            {"userId": 1, "videoId": video["id"], "requestId": "progressive-extra"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(extra["awardedPoints"], 0)
        self.assertEqual(self.snapshot(1)["user"]["points"], 7)

    def test_admin_can_upload_and_delete_unused_video_and_export_logs(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        auth = self.admin_auth()
        fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"local-test-video"
        status, uploaded = self.request(
            "POST", "/api/admin/uploads/videos",
            {"dataUrl": "data:video/mp4;base64," + base64.b64encode(fake_mp4).decode("ascii")},
            auth,
        )
        self.assertEqual(status, 201)
        self.assertTrue(uploaded["url"].startswith("/uploads/video-"))
        status, video = self.request(
            "POST", "/api/admin/videos",
            {
                "title": "待删除上传测试", "summary": "后台视频文件上传和删除闭环。",
                "coverUrl": "/assets/video-conference.svg", "mediaUrl": uploaded["url"],
                "category": "后台测试", "durationSeconds": 61, "status": "DRAFT",
            }, auth,
        )
        self.assertEqual(status, 201)
        self.assertEqual(video["total_points"], 1)
        status, deleted = self.request("DELETE", f"/api/admin/videos/{video['id']}", headers=auth)
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])

        status, content, headers = self.request_bytes("/api/admin/logs/export", auth)
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        decoded = content.decode("utf-8-sig")
        self.assertIn("操作时间,账号,操作人", decoded)
        self.assertIn("VIDEO", decoded)
        (ROOT / "web" / uploaded["url"].lstrip("/")).unlink(missing_ok=True)

    def test_admin_point_adjustment_is_auditable_and_idempotent(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        _, login = self.request(
            "POST", "/api/admin/login", {"username": "admin", "password": "dev-only-password"}
        )
        auth = {"Authorization": f"Bearer {login['token']}"}
        payload = {"userId": 1, "amount": 7, "reason": "线下活动补录", "requestId": "adjust-once"}
        status, first = self.request("POST", "/api/admin/points/adjust", payload, auth)
        self.assertEqual(status, 200)
        status, replay = self.request("POST", "/api/admin/points/adjust", payload, auth)
        self.assertEqual(status, 200)
        self.assertEqual(first, replay)
        self.assertEqual(self.snapshot(1)["user"]["points"], 12)
        status, ledger = self.request("GET", "/api/admin/point-transactions?userId=1", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(ledger["items"][0]["description"], "线下活动补录")

    def test_admin_gift_and_inventory_sync_to_user_app(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        auth = self.admin_auth()
        status, gift = self.request(
            "POST", "/api/admin/gifts",
            {"name": "到店体验礼", "description": "只在门店领取", "pointsCost": 9,
             "marketPrice": 30, "imageKey": "gift", "imageUrl": "/assets/gift-soap.svg", "status": "ACTIVE",
             "storeId": 1, "availableQuantity": 2}, auth,
        )
        self.assertEqual(status, 201)
        self.assertIn("到店体验礼", [item["name"] for item in self.snapshot(1)["gifts"]])
        status, inventory = self.request(
            "PATCH", f"/api/admin/inventory/1/{gift['id']}", {"availableQuantity": 6}, auth
        )
        self.assertEqual(status, 200)
        self.assertEqual(inventory["availableQuantity"], 6)

    def test_comprehensive_admin_watch_member_and_gift_image_management(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        auth = self.admin_auth()
        self.watch_video_one(1, "admin-detail")

        status, watch = self.request("GET", "/api/admin/watch-details", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(watch["summary"]["totalValidSeconds"], 45)
        self.assertEqual(watch["items"][0]["heartbeat_count"], 9)
        self.assertEqual(watch["items"][0]["earned_points"], 1)
        self.assertIsNotNone(watch["items"][0]["last_watched_at"])

        status, member = self.request(
            "PATCH", "/api/admin/users/1",
            {"name": "王阿姨", "mobile": "13800000000", "tags": "重点邀约,已到店",
             "notes": "下周回访", "status": "ACTIVE"}, auth,
        )
        self.assertEqual(status, 200)
        self.assertEqual(member["mobile"], "13800000000")
        status, member_detail = self.request("GET", "/api/admin/users/1", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(member_detail["user"]["tags"], "重点邀约,已到店")

        png = b"\x89PNG\r\n\x1a\n" + b"test-image"
        status, uploaded = self.request(
            "POST", "/api/admin/uploads/images",
            {"dataUrl": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}, auth,
        )
        self.assertEqual(status, 201)
        self.assertTrue(uploaded["url"].startswith("/uploads/gift-"))
        status, gift = self.request(
            "POST", "/api/admin/gifts",
            {"name": "图片可更换礼品", "description": "到店领取", "pointsCost": 6,
             "marketPrice": 20, "imageKey": "gift", "imageUrl": uploaded["url"],
             "imageUrls": [uploaded["url"], "/assets/gift-soap.svg"],
             "status": "ACTIVE", "storeId": 1, "availableQuantity": 3}, auth,
        )
        self.assertEqual(status, 201)
        online = next(item for item in self.snapshot(1)["gifts"] if item["id"] == gift["id"])
        self.assertEqual(online["image_url"], uploaded["url"])
        self.assertEqual(online["image_urls"], [uploaded["url"], "/assets/gift-soap.svg"])
        status, _ = self.request("PATCH", f"/api/admin/gifts/{gift['id']}", {"status": "INACTIVE"}, auth)
        self.assertEqual(status, 200)
        self.assertNotIn(gift["id"], [item["id"] for item in self.snapshot(1)["gifts"]])
        (ROOT / "web" / uploaded["url"].lstrip("/")).unlink(missing_ok=True)

    def test_public_snapshot_does_not_expose_operations_data(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        public = self.snapshot(1)
        for forbidden_key in ["users", "staffPickups", "adminLogs"]:
            self.assertNotIn(forbidden_key, public)
        self.assertEqual(public["invitations"], [])

    def test_user_session_identifies_user_without_frontend_user_id(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        status, login = self.request("POST", "/api/auth/dev-login", {"userId": 1})
        self.assertEqual(status, 200)
        user_auth = {"Authorization": f"Bearer {login['token']}"}
        status, data = self.request("GET", "/api/snapshot", headers=user_auth)
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["id"], 1)
        status, error = self.request("GET", "/api/admin/videos", headers=user_auth)
        self.assertEqual(status, 401)
        self.assertEqual(error["code"], "UNAUTHORIZED")

    def test_concurrent_duplicate_point_adjustment_only_applies_once(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        auth = self.admin_auth()
        payload = {"userId": 1, "amount": 4, "reason": "并发测试", "requestId": "same-key"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda _: self.request("POST", "/api/admin/points/adjust", payload, auth), range(2))
            )
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(self.snapshot(1)["user"]["points"], 9)

    def test_dashboard_config_logs_and_simple_roles(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        auth = self.admin_auth()
        status, dashboard = self.request("GET", "/api/admin/dashboard", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["counts"]["users"], 1)

        status, descriptions = self.request("GET", "/api/admin/page-descriptions", headers=auth)
        self.assertEqual(status, 200)
        self.assertIn("videos", descriptions["values"])
        status, changed = self.request(
            "PATCH", "/api/admin/page-descriptions",
            {"values": {"videos": "公司内部自定义的知识视频说明。"}}, auth,
        )
        self.assertEqual(status, 200)
        self.assertEqual(changed["values"]["videos"], "公司内部自定义的知识视频说明。")
        status, updated = self.request(
            "PATCH", "/api/admin/configs",
            {"values": {"pickup_valid_days": 10, "customer_service_phone": "0371-12345678"}}, auth
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["values"]["pickup_valid_days"], "10")
        self.assertEqual(updated["values"]["customer_service_phone"], "0371-12345678")
        self.assertEqual(self.snapshot(1)["settings"]["customerServicePhone"], "0371-12345678")
        status, logs = self.request("GET", "/api/admin/logs", headers=auth)
        self.assertEqual(status, 200)
        self.assertTrue(any(item["target_type"] == "CONFIG" for item in logs["items"]))

        status, verifier = self.request(
            "POST", "/api/admin/login", {"username": "verifier", "password": "Verifier123!"}
        )
        self.assertEqual(status, 200)
        verifier_auth = {"Authorization": f"Bearer {verifier['token']}"}
        status, error = self.request("GET", "/api/admin/configs", headers=verifier_auth)
        self.assertEqual(status, 403)
        self.assertEqual(error["code"], "FORBIDDEN")
        status, _ = self.request("GET", "/api/admin/pickups", headers=verifier_auth)
        self.assertEqual(status, 200)

    def test_storage_assets_require_delay_and_recheck_references(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        auth = self.admin_auth()
        storage_ref = "oss://test-bucket/knowledge-points/videos/2026/08/30/test.mp4"
        objects = [{
            "storageRef": storage_ref, "kind": "VIDEO", "size": 1024,
            "lastModified": "2026-08-30T00:00:00+00:00",
        }]
        with patch("app.server.oss_enabled", return_value=True), patch(
            "app.server.list_media_objects", return_value=objects
        ), patch("app.server.delete_storage_object") as delete_mock:
            status, listing = self.request("GET", "/api/admin/storage/assets", headers=auth)
            self.assertEqual(status, 200)
            self.assertEqual(listing["summary"]["unbound"], 1)

            status, _ = self.request(
                "POST", "/api/admin/storage/assets/cleanup-request", {"storageRef": storage_ref}, auth
            )
            self.assertEqual(status, 200)
            status, error = self.request(
                "POST", "/api/admin/storage/assets/delete", {"storageRef": storage_ref}, auth
            )
            self.assertEqual(status, 409)
            self.assertEqual(error["code"], "ASSET_NOT_READY")

            with database() as connection:
                connection.execute(
                    "UPDATE media_assets SET status='READY_DELETE', delete_after=? WHERE storage_ref=?",
                    ("2020-01-01T00:00:00+00:00", storage_ref),
                )
                connection.execute("UPDATE videos SET media_url=? WHERE id=1", (storage_ref,))
            status, _ = self.request(
                "POST", "/api/admin/storage/assets/delete", {"storageRef": storage_ref}, auth
            )
            self.assertEqual(status, 409)
            delete_mock.assert_not_called()

            with database() as connection:
                connection.execute("UPDATE videos SET media_url='' WHERE id=1")
                connection.execute(
                    "UPDATE media_assets SET status='READY_DELETE', delete_after=? WHERE storage_ref=?",
                    ("2020-01-01T00:00:00+00:00", storage_ref),
                )
            status, _ = self.request(
                "POST", "/api/admin/storage/assets/delete", {"storageRef": storage_ref}, auth
            )
            self.assertEqual(status, 200)
            delete_mock.assert_called_once_with(storage_ref)

    def test_admin_can_manage_stores_staff_and_own_password(self) -> None:
        self.request("POST", "/api/dev/reset", {})
        auth = self.admin_auth()
        status, store = self.request(
            "POST", "/api/admin/stores",
            {"name": "郑州体验中心", "address": "郑州市测试路1号", "contactPhone": "0371-12345678",
             "businessHours": "09:00–18:00", "status": "ACTIVE"}, auth,
        )
        self.assertEqual(status, 201)
        status, stores = self.request("GET", "/api/admin/stores", headers=auth)
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == store["id"] for item in stores["items"]))
        status, gift = self.request(
            "POST", "/api/admin/gifts",
            {"name": "二店礼品", "description": "仅二店领取", "pointsCost": 2, "marketPrice": 10,
             "imageKey": "gift", "imageUrl": "/assets/gift-soap.svg", "status": "ACTIVE", "storeId": store["id"],
             "availableQuantity": 3}, auth,
        )
        self.assertEqual(status, 201)
        public = self.snapshot(1)
        self.assertTrue(any(item["id"] == store["id"] for item in public["stores"]))
        self.assertEqual(next(item for item in public["gifts"] if item["id"] == gift["id"])["store_id"], store["id"])

        status, created = self.request(
            "POST", "/api/admin/staff",
            {"username": "store2", "displayName": "二店核销员", "role": "STORE_VERIFIER",
             "storeId": store["id"], "password": "StorePass2026"}, auth,
        )
        self.assertEqual(status, 201)
        status, login = self.request(
            "POST", "/api/admin/login", {"username": "store2", "password": "StorePass2026"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(login["staff"]["storeId"], store["id"])

        status, _ = self.request(
            "POST", "/api/admin/me/password",
            {"currentPassword": "dev-only-password", "newPassword": "NewAdmin2026"}, auth,
        )
        self.assertEqual(status, 200)
        status, _ = self.request(
            "POST", "/api/admin/login", {"username": "admin", "password": "NewAdmin2026"}
        )
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
