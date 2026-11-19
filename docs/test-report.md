# Test report

Last run: 2026-08-30. Everything below was executed against the browser build with the SQLite development database.

## 1. Automated business tests

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-tests.ps1
```

Result: **19 Python tests passed, 3 mini program JavaScript tests passed.**

Coverage:

- video upload, publish, edit, unpublish and delete;
- one point per verified minute, bounded by the duration-derived total;
- duplicate watch heartbeats and replayed requests not paying twice;
- gift inventory, pickup creation, cancellation refund and store verification;
- invitation binding exactly once, self-invitation rejection and reward transactions;
- permission isolation between administrator and store verifier;
- user endpoints not exposing console data;
- two concurrent identical point adjustments taking effect only once;
- log export and audit trail for important operations;
- store creation, verifier account creation, role binding and console password change.

One cross-layer assertion is worth calling out: publishing a video in the console makes it appear in the user snapshot immediately, and unpublishing removes it just as immediately. That proves console, API and database are genuinely wired together rather than mocked.

## 2. Database backup and restore

```powershell
python tests\database_backup_restore_test.py
```

Result: `DATABASE_BACKUP_RESTORE_OK`. All 20 business tables in the restored copy match the source row counts, and `PRAGMA integrity_check` returns `ok` on both.

The helper uses SQLite's Online Backup API (`app/backup.py`) instead of copying a live file, which is the documented way to obtain a consistent snapshot of a running database — see <https://www.sqlite.org/backup.html>.

## 3. Read-only concurrency test

```powershell
python tests\runtime_load_test.py --base-url http://127.0.0.1:8765 --concurrency 5,20,50 --requests-per-stage 200
python tests\runtime_load_test.py --base-url http://127.0.0.1:8765 --concurrency 100 --requests-per-stage 300 --p95-limit-ms 2000
```

Only read endpoints (health check and console landing page) are used, so the test cannot mutate business data.

| Concurrency | Requests | Error rate | Throughput | P95 | P99 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 5 | 200 | 0% | 56.13 req/s | 107.27 ms | 476.72 ms |
| 20 | 200 | 0% | 212.78 req/s | 149.28 ms | 235.68 ms |
| 50 | 200 | 0% | 99.69 req/s | 790.20 ms | 1447.54 ms |
| 100 | 300 | 0% | 96.74 req/s | 1285.09 ms | 1345.32 ms |

P95 means 95% of requests completed within that time. The pass condition is an error-rate ceiling plus a latency percentile rather than an average, because averages hide slow requests — the same approach k6 documents for thresholds (<https://grafana.com/docs/k6/latest/using-k6/thresholds/>).

## 4. What these numbers do and do not prove

The test environment suits development integration and small-scale trials; it is not a production capacity proof.

- 100 concurrent read requests produced no errors, but P95 rose to about 1.29 s.
- The server is Python's threaded standard-library HTTP server over SQLite. WAL improves read/write concurrency, but writes still serialise on one database file.
- The container was capped at 256 MB of memory during these runs.
- Video and images upload directly to object storage (5 GB per video); the backend only issues credentials and stores object references.
- Object storage is a private bucket read through time-limited signed URLs; no CDN is enabled yet.
- Database backups are taken daily, integrity-checked and written to private object storage; monitoring, alerting and restore drills are still missing.
- The load test ran over an SSH tunnel, so the numbers include tunnel and local network overhead and must not be read as an SLA.

For a real launch the intended direction is: object storage plus CDN for media, a production WSGI/ASGI process for the API, and MySQL 8 InnoDB for row-level locking and multi-version concurrency. After migration the read/write mixed load test, failure-restart test and backup-restore drill should all be repeated.

## 5. Remaining work before a real release

1. Register the API domain as a legal request/upload/download domain in the WeChat platform.
2. Upload a new trial build and run the full device loop: `wx.login` → backend `openid` exchange → console content creation → phone sees it → watch and earn → in-store pickup → console verification.
3. Enter real stores, verifier accounts, videos and gifts through the console.
4. Upload production media to object storage instead of the application disk.
5. Migrate to MySQL, enable automated backups and monitoring, then repeat the write-heavy concurrency test.

## 6. Test tooling added with this report

- `tests/runtime_load_test.py` — read-only concurrency test with explicit error-rate and P95 thresholds.
- `tests/database_backup_restore_test.py` — online backup, restore into a copy, row-count comparison.
- `scripts/run-tests.ps1` — runs the business suite and the backup/restore check together.
