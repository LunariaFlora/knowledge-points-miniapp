# Domain model and database design

Database baseline: MySQL 8.0 (single application, single database).
Executable definitions: `database/schema.mysql.sql` (production target) and `app/schema.sqlite.sql` (development).

## 1. Why it is shaped this way

Think of the database as a set of constrained internal ledgers: `users` registers people, `videos` registers content, `point_transactions` registers every credit and debit, `pickup_requests` registers who will collect what at which store. The system does not merely store "points remaining" — it keeps the reason behind every step, so a dispute can be reconstructed.

A relational database was chosen because deducting points, locking stock and creating a pickup request must all succeed or all fail. That is what a database transaction provides.

## 2. Core objects

| Object | Table | Purpose |
|---|---|---|
| WeChat user | `users` | WeChat identity, nickname, phone-authorisation state, account state |
| Staff account | `staff_users` | Two fixed roles: administrator, store verifier |
| Store | `stores` | Address, contact details, opening state |
| Category / video | `video_categories`, `videos` | Educational content and scoring parameters |
| Watch session | `video_watch_sessions` | When a real playback started and its last valid heartbeat |
| Watch slot | `video_watch_ticks` | One 5-second position; unique per user, video and slot |
| Video progress | `user_video_progress` | Deduplicated verified seconds and reward count |
| Point account / ledger | `point_accounts`, `point_transactions` | Balance, and the evidence for every change |
| Gift / store inventory | `gifts`, `store_gift_inventory` | Gift data and quantity per store |
| Pickup request | `pickup_requests` | Points deducted, store, contact, voucher, current state |
| Pickup status log | `pickup_status_logs` | Every state transition of a request |
| Store lead | `store_leads` | Follow-up list for the store to contact visitors |
| Invitation | `invitations` | Who invited whom, and whether the reward was paid |
| System config | `system_configs` | Adjustable values such as rewards and voucher validity |
| Admin operation log | `admin_operation_logs` | Who changed what in the console |
| Idempotency record | `idempotency_records` | Request digest and first result, reused on network retries |

Key relationships:

- A user has exactly one point account and many transactions.
- A video has many watch sessions; one user can hold only one valid row per 5-second slot of a given video.
- A gift can be stocked by several stores; a store can stock several gifts.
- A pickup request belongs to exactly one user, gift and store, and carries an unguessable voucher code.
- A new user binds at most one inviter, and an invitation pays out at most once.
- Every user gets a fixed random invite code at creation; sharing carries only the code, never the WeChat identity.

## 3. Data rules for the three loops

### 3.1 Watch points

1. Playback start creates a `video_watch_sessions` row.
2. The client sends a heartbeat every 5 seconds; the server validates interval, playback position, paused state, foreground state and playback rate.
3. On success it attempts to insert `video_watch_ticks`; a unique constraint prevents paying the same position twice.
4. A new valid slot increments `user_video_progress.valid_seconds`.
5. When verified progress reaches the duration, total points are `round(duration_seconds / 60)`; a single income transaction is written and the balance updated.
6. Issuance uses a unique business key, so retries cannot pay twice. There is no daily cap.

The scoring period must be a multiple of 5 seconds. Only normal-rate playback scores in the first version, which avoids mixing "wall-clock time" and "media position" semantics.

### 3.2 Gift pickup

Creating a request performs, inside one transaction:

1. Check user state, points, store state, inventory and redemption limits.
2. Deduct `point_accounts.balance`.
3. Insert a negative-value transaction.
4. Move store quantity from `available_quantity` to `locked_quantity`.
5. Create `pickup_requests`, the first status log and the `store_leads` row.

Verification moves locked quantity into collected quantity. Cancellation, rejection or expiry writes a refund transaction and returns locked quantity to available. History is never deleted.

### 3.3 Invitations

1. On first login the invitee binds an inviter; `invitee_user_id` is unique, so re-binding is impossible.
2. Self-invitation is rejected. `source_type`, `source_value` and `landing_scene` record whether the entry was a QR code, a share link or an activity page.
3. Once the invitee accumulates one verified minute and receives points, the invitation moves from `BOUND` to a qualified state.
4. Both reward transactions are written in one transaction, then the state becomes `REWARDED`.
5. The two transactions use separate unique business keys, so duplicate callbacks cannot double-pay.

Defaults: 50 points to the inviter, 10 to the invitee. Changing the values affects only future rewards; historical transactions keep their snapshot.

First-version configuration keys:

| Key | Default | Purpose |
|---|---:|---|
| `invite.inviter_reward_points` | 50 | Points to the inviter after a qualifying invite |
| `invite.invitee_reward_points` | 10 | Points to the new user |
| `pickup.expiry_days` | 7 | Days before a pickup request expires automatically |

These live in `system_configs`; the values are not scattered across pages. A change affects only later business, while existing requests and transactions keep the values they were created with.

## 4. State machines

Pickup requests may only move forward:

```text
WAIT_CONTACT -> WAIT_ARRIVAL -> PICKED_UP
WAIT_CONTACT -> CANCELLED / REJECTED / EXPIRED
WAIT_ARRIVAL -> CANCELLED / REJECTED / EXPIRED
```

Store leads: `NEW -> CONTACTED -> ARRIVED -> CLOSED`. A lead tracks follow-up and does not replace pickup state.

Watch sessions: `ACTIVE -> CLOSED` or `ACTIVE -> ABNORMAL`.

Invitations: `BOUND -> QUALIFIED -> REWARDED`, optionally `INVALID` when abuse is found — never deleted.

## 5. A complete example

A user watches a 95-second video:

- The duration is 1.583 minutes, so the total is 2 points after rounding.
- The server issues 1 point once a full verified minute is reached, then tops up the remainder at the end, for at most 2 points. Replaying the video does not pay again.
- The user requests a 100-point gift at store B.
- One transaction moves the balance from 100 to 0, available stock from 8 to 7, locked stock from 0 to 1, and generates a voucher.
- After the store contacts the user the request becomes `WAIT_ARRIVAL`; on verification locked stock goes 1 → 0 and collected quantity 12 → 13.

If the user cancels before verification, the system writes a "pickup cancelled, +100" transaction, the balance returns to 100 and available stock returns to 8. The original deduction remains in the ledger.

## 6. Key constraints

- `users.openid` unique — one WeChat identity cannot create several accounts.
- `users.invite_code` unique — every user has a non-conflicting random code.
- `video_watch_ticks(user_id, video_id, tick_no)` unique — the same video position cannot be counted twice.
- `point_transactions.idempotency_key` unique — one business request cannot move points twice.
- `invitations.invitee_user_id` unique — one invitee binds one inviter.
- `pickup_requests.request_no` and `voucher_code` unique.
- `idempotency_records(actor_type, actor_id, operation, idempotency_key)` unique — duplicate requests reuse the first result.
- Points, available stock and locked stock may never go negative.
- No address, courier, tracking or shipment tables exist.

## 7. Personal-information boundary

- `openid`, phone numbers, contact names and staff password digests are never returned to unauthorised pages.
- The name and phone number on a pickup request are the snapshot the user authorised at request time, so a later profile edit cannot stop the store from verifying.
- A store verifier can query only their own store's requests and leads; an administrator can read across stores.
- Account deletion anonymises identity instead of removing point and pickup evidence, which is retained as required for accounting and operations.

## 8. Conclusion of this step

All three loops have explicit data landing points; watch positions, point business, invitation binding and vouchers all carry anti-duplication constraints; deduction, stock locking, request creation and refunds are defined as transactions; the model contains no shop payment, address, courier or shipment concepts; and the two fixed console roles satisfy the current permission needs.

## Addendum, 2026-08-31: video fields

Think of a video as a content card: `category` is the drawer it lives in, `tags` are sticky notes on the card, `duration_seconds` is the objective length of the file, and `total_points` is the reward cap an administrator confirms.

- `videos.tags`: comma-separated text, empty by default; existing videos need no migration.
- `videos.duration_seconds`: read from the uploaded file by the browser at upload time; read-only in the console.
- `videos.total_points`: defaults to the rounded duration and is adjustable, always a non-negative integer.
- Page help text stays in `system_configs` under `admin_description_<page>`; it changes console wording only and never touches historical business data.

Example: a 313-second video is categorised as health knowledge with tags `pulmonary nodule, evidence-based medicine`, proposing 5 points. An administrator may lower the cap to 4; users still earn 1 point per full minute, up to 4.
