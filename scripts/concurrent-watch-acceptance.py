"""建立50位明确标记的压测用户，并同时向真实观看心跳接口发请求。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8770")
    parser.add_argument("--users", type=int, default=50)
    args = parser.parse_args()

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    video = connection.execute(
        "SELECT id, title FROM videos WHERE status='PUBLISHED' AND duration_seconds>=10 ORDER BY id LIMIT 1"
    ).fetchone()
    if not video:
        raise RuntimeError("没有可用于并发测试的已发布视频")
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
    batch = str(int(time.time()))
    actors: list[dict] = []
    for index in range(1, args.users + 1):
        invite_code = f"LOAD{index:03d}"
        row = connection.execute("SELECT id FROM users WHERE invite_code=?", (invite_code,)).fetchone()
        if row:
            user_id = int(row["id"])
            connection.execute(
                "UPDATE users SET name=?, tags=?, notes=?, status='ACTIVE' WHERE id=?",
                (f"并发观看测试用户{index:02d}", "验收测试,50人并发", "自动并发观看验收数据", user_id),
            )
        else:
            user_id = int(connection.execute(
                """INSERT INTO users (name, invite_code, tags, notes, points)
                   VALUES (?, ?, '验收测试,50人并发', '自动并发观看验收数据', 0)""",
                (f"并发观看测试用户{index:02d}", invite_code),
            ).lastrowid)
        raw_user_token = f"load-user-{batch}-{index}"
        connection.execute(
            "INSERT OR REPLACE INTO user_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (hashlib.sha256(raw_user_token.encode()).hexdigest(), user_id, expires_at),
        )
        watch_token = f"load-watch-{batch}-{index}"
        connection.execute(
            """INSERT INTO watch_sessions
               (token, user_id, video_id, status, last_position_seconds, counted_view, last_heartbeat_at)
               VALUES (?, ?, ?, 'ACTIVE', 0, 0, ?)""",
            (watch_token, user_id, int(video["id"]), time.time() - 5),
        )
        actors.append({"userId": user_id, "watchToken": watch_token, "index": index})
    connection.commit()
    connection.close()

    def heartbeat(actor: dict) -> dict:
        body = json.dumps({
            "sessionToken": actor["watchToken"],
            "requestId": f"load-heartbeat-{batch}-{actor['index']}",
            "positionSeconds": 5,
            "playing": True,
            "pageVisible": True,
            "playbackRate": 1,
        }).encode()
        request = urllib.request.Request(
            f"{args.base_url.rstrip('/')}/api/watch/heartbeat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read()
                return {"ok": response.status == 200, "status": response.status, "ms": (time.perf_counter() - started) * 1000}
        except urllib.error.HTTPError as error:
            return {"ok": False, "status": error.code, "error": error.read().decode(errors="replace"), "ms": (time.perf_counter() - started) * 1000}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "status": 0, "error": str(error), "ms": (time.perf_counter() - started) * 1000}

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.users) as executor:
        results = [future.result() for future in as_completed([executor.submit(heartbeat, actor) for actor in actors])]
    total_ms = (time.perf_counter() - started) * 1000
    latencies = [item["ms"] for item in results]

    verify = sqlite3.connect(args.database)
    counted = verify.execute(
        "SELECT COUNT(*) FROM watch_sessions WHERE token LIKE ? AND counted_view=1", (f"load-watch-{batch}-%",)
    ).fetchone()[0]
    progress = verify.execute(
        """SELECT COUNT(*) FROM watch_progress wp JOIN users u ON u.id=wp.user_id
           WHERE u.invite_code LIKE 'LOAD%' AND wp.video_id=? AND wp.valid_seconds>=5""",
        (int(video["id"]),),
    ).fetchone()[0]
    verify.close()
    failed = [item for item in results if not item["ok"]]
    print(json.dumps({
        "requestedUsers": args.users,
        "successfulRequests": len(results) - len(failed),
        "failedRequests": len(failed),
        "countedViewsInDatabase": counted,
        "usersWithValidProgress": progress,
        "totalBatchMs": round(total_ms, 1),
        "averageMs": round(statistics.mean(latencies), 1),
        "p50Ms": round(percentile(latencies, .50), 1),
        "p95Ms": round(percentile(latencies, .95), 1),
        "video": video["title"],
        "sampleErrors": failed[:3],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
