# Omni-Channel Inbox — Handoff

Instagram Direct + Facebook Messenger inbox for the `CALL_CENTER` role, across
`CRM-api-1`, `CRM-project`, `crm_mobile`, and `CRM-admin-panel`.

**Read this whole file before touching the feature.** The original plan is at
`C:\Users\ASUS\.claude\plans\continueing-working-on-the-hazy-kahn.md` — it
contains the Meta dashboard runbook and the design rationale, but §8.5 of it is
**wrong** (it claims mobile has no counterpart; mobile is now built) and §8.1's
component-genericization recommendation was deliberately not followed (see
"Deviations").

---

## What this feature does

A call-center agent sees inbound Instagram/Messenger DMs in an inbox, chats with
the person, and — when they're genuinely interested — converts the conversation
into a CRM lead assigned to an employee. The uChat pattern.

**The single most important design decision:** a conversation is **not** a lead.
`SocialConversation.client` stays `NULL` until an agent explicitly converts.
This differs from WhatsApp, where `ensure_client_for_whatsapp_phone` auto-creates
a `Client` per inbound phone number. Do not add auto-creation here — most DMs
never deserve a lead, and each one would charge the plan's `max_clients` quota.

---

## Verified state

| Surface | Command | Result |
|---|---|---|
| Backend | `.\.venv\Scripts\python.exe -m pytest` | **1290 passed, 0 failed** (~29 min) |
| Tenant web | `npm run build` (CRM-project) | clean |
| Mobile | `flutter analyze` | no issues |
| Admin panel | `npm run build` (CRM-admin-panel) | clean |

Pre-existing TypeScript errors exist in both web repos (no typecheck script is
configured in either). Baseline: 40 in `CRM-project`, 5 in `CRM-admin-panel`
(`Dashboard.tsx`, `Reports.tsx`). **None are in files this feature touched** —
verify with `npx tsc --noEmit | grep -oE "^[^(]+\.tsx?" | sort -u` before
blaming yourself for a new one.

**Nothing has ever run against a real Meta app.** All Graph calls are mocked in
tests. See "Blockers" below.

### Migrations (all applied locally)
`integrations/0049_meta_inbox_connection`, `integrations/0050_social_inbox_models`,
`accounts/0043_supervisor_manage_social_inbox`,
`notifications/0021_social_notification_types`, `crm/0059_client_source_social`.

### Test files (214 tests)
`integrations/tests/test_meta_inbox_{connections,webhook,ingest,acl,send,convert,maintenance,lead_messages}.py`
plus additions to `tests/test_call_center_permissions.py` and
`tests/test_sync_digest.py`.

**The suite takes ~25–30 minutes.** ~1.3s per test, single-process, and the test
database is rebuilt through 281 migrations every session. `--reuse-db` keeps
`test_db.sqlite3` between runs and removes most of that (pass `--create-db` after
adding a migration). Not in `pytest.ini` addopts by choice — a stale reused DB is a
worse failure mode than a slow run for anyone who does not know the flag is on.

---

## Blockers — do these first

1. ~~**`.env` credentials are empty.**~~ **Resolved.** `META_INBOX_CLIENT_ID`,
   `META_INBOX_CLIENT_SECRET` and
   `META_INBOX_FACEBOOK_LOGIN_FOR_BUSINESS_CONFIG_ID` are now all filled in, so
   the OAuth dialog can open.
   - `META_INBOX_WEBHOOK_VERIFY_TOKEN` is already set.
   - `META_INBOX_REDIRECT_URI` in `.env` is **inert** — settings derives it from
     `API_BASE_URL`. Editing it does nothing.

2. **Webhook not yet registered with Meta.** Meta calls `hub.challenge` before it
   will let you save the callback URL, so the endpoint must be deployed first.
   Register **two objects against the same URL** `/api/integrations/webhooks/meta-inbox/`:
   - object `instagram`: `messages`, `messaging_postbacks`, `message_reactions`,
     `messaging_seen`, `messaging_referral`, `standby`
   - object `page`: `messages`, `messaging_postbacks`, `message_deliveries`,
     `message_reads`

3. **App Review.** `instagram_manage_messages` + `pages_messaging` at Advanced
   Access need business verification plus a screencast. Everything is testable in
   Development mode with role-added test users, so engineering is not blocked —
   only production launch is.

### Meta app state (already done by the user)
App created with use cases *"Engage with customers on Messenger from Meta"* and
*"Manage messaging & content on Instagram"* (**not** WhatsApp — it lives on the
other app). Facebook Login for Business configuration created with assets
**Pages (required) + Instagram accounts (optional)** and six permissions:
`pages_messaging`, `pages_manage_metadata`, `pages_show_list`, `instagram_basic`,
`instagram_manage_messages`, `pages_read_engagement` (the last auto-added as a
dependency of `instagram_basic`).

> Gotcha already hit: the permission picker only offers the Messenger four until
> you set the Instagram use case to **"API setup with Facebook Login"** and click
> **"Add required messaging permissions"**.

---

## Invariants — breaking these is a regression

1. **CALL_CENTER is allowed here and denied on WhatsApp, simultaneously.**
   `tests/test_call_center_permissions.py::TestCallCenterSocialInboxAllowed`
   pins both halves. Do not "harmonize" them. The inbox endpoints are function
   views with their own `CanUseSocialInbox`; they must never move onto
   `ClientViewSet`, or `DenyCallCenterNonLeadAPI` would have to be weakened.

2. **Denied reads answer 404, never 403.** A 403 would confirm a conversation
   exists to a scoped employee.

3. **The webhook signature binds to `META_INBOX_CLIENT_SECRET` only.** No
   fallback to `META_CLIENT_SECRET` (unlike `whatsapp_webhook.py`, which does
   fall back). A fallback would let the Lead Ads app's secret forge inbox
   messages. There is an explicit test.

4. **Never fabricate a phone number on convert.** IG/Messenger carry none. A
   placeholder would consume the company-wide unique phone key
   (`uniq_company_phone_normalized`). Phone-less leads are correct and expected.

5. **Never send outside the messaging window.** No templates exist for these
   channels — only the `HUMAN_AGENT` tag (7 days), gated behind
   `META_INBOX_HUMAN_AGENT_TAG_ENABLED` which stays `false` until Meta approves
   the Human Agent feature. Sends are refused locally *before* any Graph call.

6. **Bulk `update()` fires no signal.** `social_mark_conversation_read` bumps the
   `inbox` slice explicitly. Any new bulk write must do the same or the list
   will 304 with stale data.

---

## Known gaps — prioritized

### P1 — correctness — **all three done**

Tests: `integrations/tests/test_meta_inbox_maintenance.py` (37).

- ~~**`purge_social_media` management command does not exist.**~~ Built. Deletes
  stored attachment bytes past `META_INBOX_MEDIA_RETENTION_DAYS` (default 90,
  blank-safe like `META_INBOX_MAX_MEDIA_BYTES`); `--days`, `--dry-run`,
  `--company-id`. Message rows, captions and `attachment_kind` are kept, so it is
  a storage bound and not a privacy control. Crontab #21, daily 3:56.
  - **Still open:** inbox media is charged against no plan quota.
    `_company_storage_used()` in `company_library/views.py` counts
    `CompanyLibraryFile.size_bytes` and nothing else — not WhatsApp media either.
    Wiring `SocialMessage.attachment_size` into it is a product decision, not a
    cleanup: on ingest the only options are to silently drop inbound media or to
    let tenants exceed the quota, and counting it would also push existing tenants
    over their library quota overnight.
- ~~**No page-token health job.**~~ Built:
  `manage.py check_meta_inbox_connections`, crontab #20, daily 6:37. Sweeps every
  non-`disconnected` connection through `check_connection_health`. Two things it
  deliberately does *not* do: it does not alert on a Graph error (that is an
  unknown answer, not a broken Page — alerting would email every owner during a
  Meta outage), and it notifies only on the `connected` → `error` **transition**,
  because the error status persists and state-based alerting would re-nag daily.
- ~~**`refresh_meta_inbox_page_tokens` not implemented.**~~ Built in
  `services/meta_inbox_connections.py`. Hooked into
  `token_lifecycle.apply_refreshed_token` via `sync_derived_credentials` rather
  than into the cron, so *every* path that replaces a user token re-derives page
  tokens. A per-page failure keeps the existing token — it is still the best
  credential available and the next run retries.

### P1 — found while doing the above
- **The token-refresh cron had never run, on any platform.**
  `refresh_integration_tokens` did `from integrations.tasks import
  refresh_expired_tokens, validate_meta_tokens` and **neither function was ever
  written**, so crontab #19 died with `ImportError` before doing any work. This
  predates the inbox and silently affected Meta Lead Ads and WhatsApp too. Both
  are now implemented in `services/token_lifecycle.py` (where they belong) and
  the command imports from there.
  - Scoped to `META_LIKE_PLATFORMS`. Widening it would run `get_oauth_handler` on
    tiktok/api/mujeb, which raises, and turn each raise into a false "your
    integration expired" email.
  - A failed refresh is confirmed with `debug_token` before the account is marked
    expired. `check_account_token()` returns a **three-state** answer —
    valid / invalid / *unknown* — and only an explicit Graph verdict expires an
    account. The unknown state is load-bearing: `debug_token` reports
    `is_valid: False` when it cannot obtain an **app** token, so reading that as a
    verdict would expire every account on the platform the moment an app
    credential went blank.

### P2 — parity / completeness
- ~~**Lead-Timeline `social_thread` entry is not built on either platform.**~~
  **Done, both platforms in one change set.** Details in "The lead Timeline"
  below.
- **Web `InboxPage.tsx` is simpler than `ChatsPage.tsx`**: no filter rail, no
  starred/unreplied toggles, no media viewer or voice player (attachments render
  as bare `<img>`/`<video>`/`<audio>`). The backend supports all of it.
- **No inbound sound on mobile.** WhatsApp has `assets/sounds/notif_whatsapp.wav`;
  the inbox has no equivalent.
- **IG comments / story replies** are explicitly out of v1 scope, as is folding
  WhatsApp into this inbox. Both were deliberate product decisions.

### P3 — documentation
- `.cursor/rules/social-inbox.mdc` documents the inbox's runtime rules but
  **not** the integration-registration checklist below. Worth adding.

---

## Adding an integration touches six places (learned the hard way)

A gap here is what made the admin panel unable to control this feature at first.

| Where | What |
|---|---|
| `integrations/policy.py` | `INTEGRATION_POLICY_PLATFORMS` + `PLAN_INTEGRATION_FEATURE_MAP` + disable side-effects (both the per-company and global-transition branches) |
| `subscriptions/entitlements_catalog.py` | `FEATURE_KEYS` + `DEFAULT_FEATURES` |
| `CRM-admin-panel/pages/SystemSettings.tsx` | `IntegrationPlatformKey` union, `DEFAULT_INTEGRATION_POLICIES`, `platformLabels`, the render array, **and the per-platform hydration block in `loadAll`** (easy to miss; TS catches it) |
| `CRM-admin-panel/components/PlanModal.tsx` | `entitlementsFeatures` default + a `<Checkbox>` |
| `CRM-admin-panel/context/i18n.tsx` | label key in **both** locales |
| Tenant `IntegrationsPage` | a connect entry point — the generic "Add new account" button creates the *default* platform only |

---

## Deviations from the original plan

1. **Web components were not genericized.** The plan (§8.1) called for moving
   `ConversationList`, `ChatThread`, `ChatMessageBubble`, `ChatComposer`,
   `ChatFilterRail` into `components/conversations/` and turning
   `components/whatsapp/*` into re-export shims. This was **not done**:
   it means editing ~1,700 lines of live WhatsApp chat code with no test runner
   and no way to verify the Chats page visually. `InboxPage.tsx` is instead
   self-contained on the shared `whatsappChatTheme.ts` tokens. The refactor
   remains available as an isolated follow-up whose only success criterion is
   "WhatsApp Chats page behaves identically."

2. **Mobile was built, contrary to plan §8.5.** That section claimed the inbox
   had no mobile counterpart "exactly as WhatsApp Chats was" — factually wrong.
   WhatsApp Chats has a 3,545-line Flutter implementation and CALL_CENTER already
   has `call_center_home_screen.dart`, so a web-only inbox was a real gap.

3. **Convert-to-lead (plan Phase 6) shipped before the UI (Phase 5)**, so the
   frontends were written once against a complete API.

---

## The lead Timeline is a third surface

A converted conversation also shows up on the lead's Timeline as a
`social_thread` / `socialThread` card. It reads from its own endpoint,
`GET /integrations/inbox/lead-messages/?client=` (`social_lead_messages` in
`views/social_inbox.py`), because the timeline wants every message across every
conversation on the lead and none of the thread metadata. This mirrors how
WhatsApp and SMS already feed the timeline: one endpoint per source, merged
client-side, no aggregate "timeline" API.

Three decisions worth not re-litigating:

1. **It is gated on the inbox ACL (`CanUseSocialInbox` +
   `filter_social_conversations_queryset`), not on lead access.** Reception and
   data entry can open a lead and get **403** here. That is the existing product
   rule — they get nothing from the inbox — and reusing the same filter means
   one place decides who may read a DM. Widening it would put message bodies in
   front of the roles the inbox deliberately excludes. Both clients treat 403 as
   "no social entries", never as an error.
2. **`_serialize_lead_message` is narrower than `_serialize_message`.** No
   delivery state, reactions, echo flags or attachment geometry. `attachment_kind`
   survives only because a media message has an empty body and the row would
   otherwise render blank.
3. **Rows carry `conversation` and `channel`, and the collapse breaks on a
   conversation change** — unlike the WhatsApp collapse, which groups purely on
   adjacency. A lead can hold an Instagram DM and a Messenger thread at once, and
   grouping those together would file two strangers under one heading.

Changes here are bound by `.cursor/rules/lead-timeline-parity.mdc`: web and
Flutter in **one** change set.

---

## Landmarks

**Backend** — `integrations/`: `meta_inbox_webhook.py`, `social_inbox_access.py`,
`social_conversation_state.py`, `views/social_inbox.py`, and
`services/meta_inbox_{connections,ingest,media,send}.py` + `social_lead.py` +
`social_push.py`. Models `MetaInboxConnection` / `SocialContact` /
`SocialConversation` / `SocialMessage` are at the end of `integrations/models.py`.

**Web** — `pages/InboxPage.tsx`, `components/modals/ConvertConversationModal.tsx`,
`components/integrations/SocialInboxSection.tsx`, `.cursor/rules/social-inbox.mdc`.
Timeline: `collapseConsecutiveSocialThreads` in `pages/ViewLeadPage.tsx`,
`SocialThreadBody` in `components/Timeline.tsx`,
`utils/socialMessageBodyDisplay.ts`, `useLeadSocialMessages`.

**Mobile** — `lib/features/social_inbox/`, `lib/screens/social_inbox/`,
`lib/models/social_{conversation,message}_model.dart`,
`lib/utils/social_inbox_access.dart`. Timeline:
`collapseConsecutiveSocialThreads` in `lib/utils/timeline_builder.dart`,
`_SocialThreadBody` in `lib/widgets/lead_timeline.dart`,
`lib/models/lead_social_message_model.dart`,
`lib/utils/social_message_body_localize.dart`.

**Scheduled jobs** — `integrations/management/commands/`:
`purge_social_media.py` (#21), `check_meta_inbox_connections.py` (#20),
`refresh_integration_tokens.py` (#19, delegates to `services/token_lifecycle.py`).

**Freshness** — sync slice `inbox` in `sync/version.py`, receivers in
`sync/signals.py`, badge `social_inbox_unread` in `sync/counts.py` (returns
`None` when gated, distinct from `0`). Mobile bridges the slice to its refresh
bus via `_sliceInvalidationKeys` in `lib/services/whatsapp_chat_unread_poller.dart`
using the key `social:conversations`, which `services/social_push.py` emits as
the FCM `invalidate` field.

---

## Traps

- **`conftest.py` shares one `api_client`.** `authenticated_call_center`,
  `authenticated_employee`, and `authenticated_admin` all call
  `force_authenticate` on the *same* fixture. Requesting two in one test leaves
  only the last authenticated — it surfaces as a confusing 403. Build a separate
  `APIClient()` when you need two roles.
- **Blank `.env` values are not missing values.** `os.getenv(k, default)` returns
  `''`, not the default. `META_INBOX_MAX_MEDIA_BYTES` crashed startup this way.
- **String-replace edits cascade on indentation.** A 24-space line *contains* the
  20-space pattern as a substring; replacing shortest-first double-applies. Match
  on `strip()` line-by-line instead.
- **Check before adding a translation key.** `viewLead` and `unassigned` already
  existed; duplicates are a TS1117 error caught only by `tsc`, which nothing runs
  automatically.
