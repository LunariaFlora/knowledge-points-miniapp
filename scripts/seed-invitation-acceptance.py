"""在指定数据库中建立六位可辨认的邀请验收用户，并验证邀请规则。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.server import apply_watch_tick, bind_invitation_by_code  # noqa: E402


PEOPLE = [
    ("A", "邀请验收A（第一层邀请人）", "INVTESTA"),
    ("B", "邀请验收B（A的直接下级）", "INVTESTB"),
    ("C", "邀请验收C（B的直接下级）", "INVTESTC"),
    ("D", "邀请验收D（第二组邀请人）", "INVTESTD"),
    ("E", "邀请验收E（D的直接下级）", "INVTESTE"),
    ("F", "邀请验收F（自然注册）", "INVTESTF"),
]


def ensure_user(connection: sqlite3.Connection, label: str, name: str, invite_code: str) -> int:
    row = connection.execute("SELECT id FROM users WHERE invite_code=?", (invite_code,)).fetchone()
    if row:
        user_id = int(row["id"])
        connection.execute(
            "UPDATE users SET name=?, tags=?, notes=?, status='ACTIVE' WHERE id=?",
            (name, "验收测试,邀请关系", f"六人邀请验收数据：角色{label}", user_id),
        )
        return user_id
    cursor = connection.execute(
        """INSERT INTO users (name, invite_code, tags, notes, points)
           VALUES (?, ?, '验收测试,邀请关系', ?, 0)""",
        (name, invite_code, f"六人邀请验收数据：角色{label}"),
    )
    return int(cursor.lastrowid)


def ensure_relation(
    connection: sqlite3.Connection,
    inviter_id: int,
    invitee_id: int,
    inviter_code: str,
    source_type: str,
) -> None:
    existing = connection.execute(
        "SELECT inviter_user_id FROM invitations WHERE invitee_user_id=?", (invitee_id,)
    ).fetchone()
    if existing:
        if int(existing["inviter_user_id"]) != inviter_id:
            raise RuntimeError(f"用户 {invitee_id} 已绑定其他邀请人，拒绝改写")
        return
    if not bind_invitation_by_code(
        connection,
        invitee_id,
        inviter_code,
        source_type,
        f"acceptance:{inviter_code}",
        "INVITE_ACCEPTANCE",
    ):
        raise RuntimeError(f"无法建立邀请关系 {inviter_id} -> {invitee_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    try:
        ids = {label: ensure_user(connection, label, name, code) for label, name, code in PEOPLE}
        ensure_relation(connection, ids["A"], ids["B"], "INVTESTA", "SHARE_CARD")
        ensure_relation(connection, ids["B"], ids["C"], "INVTESTB", "MINI_CODE")
        ensure_relation(connection, ids["D"], ids["E"], "INVTESTD", "QR_CODE")

        # A→B 已存在时，再尝试 B→A 必须失败，证明不会形成互邀闭环。
        reverse_rejected = not bind_invitation_by_code(
            connection, ids["A"], "INVTESTB", "SHARE_CARD", "acceptance:reverse", "INVITE_ACCEPTANCE"
        )
        if not reverse_rejected:
            raise RuntimeError("反向邀请没有被拒绝")

        video = connection.execute(
            """SELECT id, title, duration_seconds FROM videos
               WHERE status='PUBLISHED' ORDER BY duration_seconds DESC, id LIMIT 1"""
        ).fetchone()
        if not video:
            raise RuntimeError("没有已发布视频，无法触发邀请达标")
        # B 和 E 看完一条视频，触发 A/B 与 D/E 双方邀请奖励；C 保留为待达标样本。
        for user_id in (ids["B"], ids["E"]):
            for tick_no in range(1, (int(video["duration_seconds"]) + 4) // 5 + 1):
                apply_watch_tick(connection, user_id, int(video["id"]), tick_no)

        rows = [
            dict(row)
            for row in connection.execute(
                """SELECT a.name AS inviter, b.name AS invitee, i.status, i.source_type
                   FROM invitations i JOIN users a ON a.id=i.inviter_user_id
                   JOIN users b ON b.id=i.invitee_user_id
                   WHERE a.invite_code LIKE 'INVTEST%' OR b.invite_code LIKE 'INVTEST%'
                   ORDER BY i.id"""
            )
        ]
        connection.commit()
        print(json.dumps({
            "users": ids,
            "reverseInviteRejected": reverse_rejected,
            "directRelations": rows,
            "indirectRelation": "A是C的间接上级；数据库不创建A到C的直接关系，也不向A发放C的奖励",
            "rewardVideo": video["title"],
        }, ensure_ascii=False, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
