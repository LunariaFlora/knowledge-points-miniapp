# Product scope and business rules

Status: agreed with the client before engineering started.
Goal: decide what problem the system solves and how it behaves, *then* design the database and the code.

## 1. One-sentence definition

The client uses a WeChat mini program to spread health-education content; users earn points for verified watch time, redeem points for gifts they collect in person at a physical store, and can earn extra points by inviting friends. Gifts are the hook that brings interested users to an offline store — the system is not an online shop.

## 2. Roles in the first version

| Role | Can do | Cannot do |
|---|---|---|
| Visitor | Browse home, video list, gift list and public videos | Earn points, request gifts, claim invite rewards |
| WeChat user | Watch videos, earn points, view the ledger, request a gift for in-store pickup, invite friends | Change rules, credit themselves, verify gifts |
| Administrator | Manage videos, point rules, gifts, stores, users, invite leads and configuration | Modify data without leaving an operation log |
| Store verifier | Query their own store's pending pickups, contact users, check vouchers, confirm collection | Change point rules, videos, gift costs or other stores' data |

The first version keeps only these two console roles with fixed permissions; there is no role editor.

## 3. The three core loops

### 3.1 Video and points

1. An administrator creates a knowledge video with title, category, cover, playback source, scoring period and points per period.
2. Once published, the video appears in the mini program.
3. Visitors may browse; earning points requires WeChat login.
4. When playback starts, the server opens a watch session.
5. The mini program reports progress periodically. Pauses, seeks, backgrounding and abnormal playback rates do not count as verified watch time.
6. Total points derive from duration: one point per minute, rounded to the nearest minute. The user earns one point per full verified minute, never exceeding the video total; the final sub-minute remainder is topped up when playback ends.
7. Each verified interval settles exactly once. Leaving and returning continues accumulating; repeated requests never pay twice.
8. The server writes a point income transaction; the user sees balance and history in the profile.

### 3.2 Points, gifts and in-store pickup

1. An administrator creates a gift with image, point cost, per-store inventory and redemption limits.
2. The user selects a gift and a pickup store, and explicitly consents to providing the name and phone number needed for the store to contact them.
3. The server checks login state, points, store stock and redemption limits.
4. On success, point deduction, inventory locking and request creation happen in **one database transaction**.
5. A pickup voucher is generated and the user is added to that store's lead list.
6. Store staff contact the user and verify the voucher on arrival, marking the request as collected.
7. On cancellation, rejection or expiry, the system writes a point refund transaction and restores store stock. Original rows are never deleted.
8. There is no shipping, delivery address, courier, tracking number or online fulfilment state.

### 3.3 Inviting friends

1. A logged-in user generates a share entry carrying their own invite code.
2. A new user can bind an inviter on first login.
3. The invitation only succeeds after the invitee completes a qualifying action.
4. The server issues the reward and records the transactions.
5. Self-invitation, re-binding and duplicate rewards for the same invitee are rejected.

## 4. Agreed and recommended defaults

All of these are configuration values in the console, not hard-coded constants.

| Rule | Default | Status |
|---|---|---|
| Login requirement | Browsing is open; login is required to earn points, request gifts and invite | Recommended default |
| Valid watch condition | Real playback time only; pausing, seeking, backgrounding and abnormal rates do not advance progress | Agreed |
| Scoring | 1 point per full verified minute; video total = rounded minutes; remainder topped up at the end | Agreed |
| Daily points cap | None | Agreed |
| Duplicate scoring | Each verified interval settles once; repeated requests cannot add points | Mandatory |
| Invite reward trigger | Invitee logs in and completes a first verified watch | Pending confirmation |
| Invite reward value | 50 points to the inviter, 10 to the invitee | Pending confirmation |
| Gift fulfilment | In-store pickup only, no shipping | Agreed |
| Pickup request | Selecting a store generates a voucher and a store lead | Recommended default |
| Deduction timing | Deducted immediately when the request is created | Recommended default |
| Cancel / reject / expire | Refund points and restore the selected store's stock | Recommended default |
| Console permissions | Two fixed roles only | Agreed direction |

## 5. In scope for the first version

**Mini program**

- WeChat login.
- Home with education banners and gift grid.
- Video categories, list, detail and playback.
- Verified watch progress, watch rewards, balance and ledger.
- Gift catalogue and detail; store list with address and contact details.
- Store selection and pickup request creation.
- Voucher, pickup records and status.
- Invitations and invite records.
- Profile; privacy policy, user agreement and account deletion entry.

**Operations console**

- Staff login and the two fixed roles.
- Video categories and full content management (cover, upload or link, edit, delete, publish/unpublish).
- Automatic total points from duration, with an administrator-adjustable cap.
- User and point ledger queries; manual adjustment with a recorded reason.
- Store management; gifts and per-store inventory.
- Pickup requests, store leads and voucher verification.
- Invitation relationship queries.
- Basic reporting: verified watches, points, pickup requests and in-store conversion.
- Administrator operation log and system configuration.

## 6. Explicitly out of scope

Cash payment, carts and a cash shop; delivery addresses, shipping, couriers and logistics tracking; multi-merchant onboarding and settlement; live streaming; online consultation, prescriptions, medical records or health files; distribution commissions and withdrawals; a custom role-permission system; recommendation algorithms; native app, H5 or Alipay builds; microservices, message queues, big-data platforms or cluster deployment.

These are not ruled out forever — they are excluded so the first version stays finishable.

## 7. Mandatory safety rules

- Only the server decides whether points are granted, deducted or refunded; the client cannot write points.
- Verified watch time comes from server sessions and playback heartbeats; seeking, replaying reports or backgrounding must not add time.
- Every point change has a transaction, a business reason, a related object and a timestamp.
- Granting points, deducting points, locking stock and restoring stock must be protected against duplicate execution.
- Manual point adjustments record the administrator, the delta, the reason and the time.
- Pickup states follow the defined transitions and may not jump arbitrarily.
- Names, phone numbers and selected stores are used only for contact and pickup, exposed only to the roles that need them, with access and operation records kept.
- Every console write endpoint validates identity and permission.
- Health-education content requires editorial review to avoid unverified treatment, cure or efficacy claims.

## 8. Acceptance criteria for this step

Before moving to database design, confirm that: the one-sentence definition still matches the client's goal; the four roles match real operational staffing; the three loops behave as described; the agreed rules in section 4 are no longer reshaped around shop and shipping assumptions; reward values, voucher format and expiry remain adjustable; and the in-scope/out-of-scope boundary is accepted.

## Addendum, 2026-08-31: video operation rules

- Duration is read automatically from the uploaded file's metadata; operators cannot edit it in order to change points.
- The system proposes a total from duration (rounded minutes); an administrator may adjust the cap for an individual video.
- Users still earn 1 point per full verified minute and stop at the cap; the final sub-minute remainder is settled when playback completes.
- Categories accept free text. Tags are multiple content labels on the same video, used for user comprehension and later filtering, not a playlist concept.
- If the uploader provides no cover, the console captures the first frame of the local video as the cover.
- Per-page console help text is maintained by administrators; announcement broadcasting is deferred.
