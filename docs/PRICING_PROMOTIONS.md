# Plan sale prices, plan badges and a promotion bar: a portable spec

This spec is stack-agnostic. Another SaaS should be able to implement the feature from this file alone. LOOP CRM's implementation is the reference, and its file paths are listed at the end.

**What the feature delivers:**
1. **Sale prices.** A plan can have an optional discounted price per billing cycle, with an optional time window. While the sale is live, the regular price is struck through, the sale price is shown and charged, and a "−79%" pill appears. With no sale price set, the plan is an ordinary plan.
2. **Card marketing.** Each plan can have a badge (Most popular, Best value, …), a "featured" (emphasized) card style, a short offer note ("Black Friday: 2× AI credits") and a display order.
3. **Promotion bar.** A platform-wide banner across the top of pricing pages, the tenant dashboard and the mobile app. It has rotating messages, an optional countdown, a call-to-action, a theme, a schedule and an audience. **It is display only and never changes what anyone pays.**

---

## 1. Business rules (decide these first)

| Rule | Value used here | Why |
|---|---|---|
| Who is charged the sale price | New purchases, trial→paid, reactivation, **renewals** | "The price you see is the price you pay." |
| Upgrade proration | Always on **regular** prices | A sale on the higher tier must never zero out or block an upgrade |
| When a cycle is on sale | Sale price set, `0 < sale < regular`, and the window is live | An invalid or expired sale silently falls back to the regular price |
| Each cycle is independent | Monthly can be on sale while yearly is not | |
| Discount % shown | `floor((regular − sale) / regular × 100)` | Never overstate a discount |
| Free and trial plans | Cannot have a sale | A sale must never turn a paid plan into a free one |
| Sale window vs promotion window | **Independent** | Pausing or deleting a promotion must never alter billing. Instead, the admin UI warns when they differ. |
| Several live promotions | Highest `priority`, then the latest `starts_at`, wins; one bar at a time | |
| Who sees the dashboard and mobile bar | Company owners only, and not while support staff impersonate | Only owners can buy |

## 2. Data model

**Plan** (every field optional, so existing plans are unaffected):

| Field | Type | Notes |
|---|---|---|
| `sale_price_monthly`, `sale_price_yearly` | decimal, null | null means no sale on that cycle |
| `sale_starts_at`, `sale_ends_at` | datetime (UTC), null | null start means "now"; null end means "until removed" |
| `badge` | enum `"" \| most_popular \| best_value \| recommended \| new \| limited_offer \| custom` | Clients localize the preset keys |
| `badge_text`, `badge_text_ar` | string(40) | Required (both) only when `badge = custom` |
| `is_featured` | bool | Emphasized card |
| `offer_note`, `offer_note_ar` | string(160) | Both locales or neither |
| `sort_order` | int ≥ 0 | Public order is `sort_order, price, id`, so all-zero keeps the legacy order |

**Data migration.** If a client previously guessed which plan is "Most popular", seed that badge on the same plan so nothing changes visually.

**Payment.pricing** (JSON, written **only by the server** at checkout):
```json
{"v":1,"intent":"initial","billing_cycle":"monthly","regular_usd":"9.00","charged_usd":"2.49","discount_usd":"6.51","sale_ends_at":"2026-11-30T21:00:00Z"}
```

**Invoice**: `regular_amount` and `discount_amount` (null when no discount). Scale them from the USD snapshot if the gateway bills in another currency.

**Promotion:**

| Field | Notes |
|---|---|
| `name` | Internal only, never public |
| `messages` | `[{en, ar}]`, 1–6 items, ≤120 chars each, **both locales required** |
| `cta_label`, `cta_label_ar`, `cta_url` | Both labels or neither. The URL is `https://…` or a relative `/path`; blank means each surface's plans/billing page |
| `theme` | Presets `sale \| brand \| dark \| amber`, not free colours, so contrast is guaranteed in light and dark mode |
| `starts_at` (required), `ends_at` (optional) | |
| `show_countdown` | Requires `ends_at` |
| `audiences` | Subset of `public \| dashboard \| mobile`, at least 1 |
| `dismissible`, `is_active` (kill switch), `priority` | |
| `created_by`, `created_at`, `updated_at` | |

**Status is derived, never stored:**
- `paused` if not active
- `scheduled` if `starts_at` is after now
- `ended` if `ends_at` is at or before now
- otherwise `live`

## 3. Pricing engine (one module, single source of truth)

```
CyclePrice { regular, price, on_sale, discount_percent, sale_ends_at }
cycle_price(plan, cycle, at=now) -> CyclePrice
plan_pricing(plan, at) -> { monthly: CyclePrice, yearly: CyclePrice }
sale_window_status(plan, at) -> none | scheduled | live | ended
CheckoutQuote { plan, cycle, amount, intent, regular, discount, sale_ends_at, snapshot() }
```

Everything that needs a price calls this module: checkout, preview, serializers and invoices. The legacy "regular price" helper keeps its meaning.

### The price lock (the part people get wrong)

The problem: checkout charges the sale price, but the gateway webhook can land after the sale ended. If completion re-validates against the **current** price, a legitimate payment is rejected.

**Fix:**
1. At checkout, write `Payment.pricing` from the server-computed quote.
2. When the payment completes, accept the gateway-verified amount if it matches any of:
   - the locked `charged_usd` (sale ended mid-session)
   - the current effective price
   - the regular price (legacy rows without a snapshot; a sale that started mid-session)

This stays safe for two reasons:
- Every candidate amount is server-derived.
- The paid amount itself comes from server-to-gateway verification, never from the request body.

**Related details:**
- Pending-session reuse must compare amounts, so a price change opens a new gateway session.
- A pending session that has expired at the gateway cannot complete anyway.

## 4. API contract

### `GET /public/plans/` (anonymous)
Keep every existing field, with unchanged meaning (`price_monthly` stays the **regular** price), and add:
```json
{
  "pricing": {"monthly": {"regular":"12.19","price":"2.49","on_sale":true,"discount_percent":79},
              "yearly":  {"regular":"120.00","price":"120.00","on_sale":false,"discount_percent":0}},
  "sale_ends_at": "2026-11-30T21:00:00Z",
  "badge": {"key":"most_popular","text":"","text_ar":""},
  "is_featured": true, "offer_note": "…", "offer_note_ar": "…", "sort_order": 0
}
```
- `badge` is `null` when the plan has none.
- `sale_ends_at` is non-null only while the sale is live.
- Use one `now` for the whole response so every card agrees on sale state.

### `GET /public/promotions/active/?audience=public|dashboard|mobile` (anonymous)
```json
{"promotion": {"id":1,"messages":[{"en":"…","ar":"…"}],"cta_label":"See plans","cta_label_ar":"…","cta_url":"",
               "theme":"sale","starts_at":"…","ends_at":"…","show_countdown":true,"dismissible":true,"updated_at":"…"},
 "server_time": "2026-10-23T00:00:00Z"}
```
- `promotion` is `null` when nothing is live for that audience. An unknown audience falls back to `public`.
- **Send `Cache-Control: no-store`.** `server_time` is the clients' clock-skew reference. Live testing caught this bug: with `max-age=60`, a refetch served from the browser cache paired an old `server_time` with a fresh device clock, which shifted the countdown back by up to a minute. A CDN would do the same.
- Never expose `name`, `priority`, `audiences`, `is_active` or `created_by`.

### Admin (plan-manager permission)
- Plan CRUD accepts the new fields.
- `promotions/` provides CRUD, a `?status=live|scheduled|ended|paused` filter, and `POST promotions/{id}/duplicate/`, which creates a copy that **starts paused**.
- The admin plan list adds read-only `pricing` and `sale_status`.

### Preview / quote endpoint
Add `regular_amount_usd`, `discount_usd` and `sale_ends_at` next to `amount_usd`.

## 5. Validation

| Field | Rule | Error code |
|---|---|---|
| `sale_price_*` | ≥ 0.01, 2 decimals | `validation.number_range` |
| `sale_price_*` vs regular | `0 < sale < regular` (cross-field, server) | `validation.sale_price_below_regular` |
| `sale_price_*` on a free or trial plan | rejected | `validation.sale_requires_paid_plan` |
| `sale_ends_at` / `ends_at` | after the start | `validation.date_range` |
| `badge_text*` | required if `badge = custom` | `validation.required_if` |
| `offer_note*`, `cta_label*`, `messages[].en/ar` | both locales | `validation.both_locales_required` |
| `messages` | 1–6 items | `validation.array` |
| `show_countdown` | requires `ends_at` | `validation.countdown_requires_end` |
| `cta_url` | `https://…` or `/path` only (rejects `javascript:` and `http:`) | `validation.url` |
| `audiences` | ⊆ the allowed set, ≥1 | `validation.one_of` / `validation.array` |

- Cross-field rules live in the server serializer and are shown inline through the client's server-error mapping.
- The client also shows an instant inline hint when sale ≥ regular.

## 6. UI specs

### Plan card, building blocks
- **`PlanPrice`**
  - Shows the regular price as `<del>`, plus sr-only "Regular price:", only while on sale.
  - The big number is the charged price, followed by the period.
  - Reserve the strike-through row when **any** cycle is on sale, so cards don't jump when toggling monthly/yearly.
  - Put numbers in an LTR-isolated span so `$2.49` never flips inside RTL text.
- **`PlanCornerBadges`**
  - Pills on the card's top edge, at the logical **end** side: the badge (amber, or white on the dark featured card) and the `−79%` pill (rose).
  - Pass "on dark card" explicitly. Don't infer it from `is_featured`, because only one surface renders featured cards dark.
- **`PlanOfferNote`**: a tinted callout with a gift icon. Renders nothing when empty.
- **`SaleCountdownChip`**
  - In the final 7 days, a ticking "Offer ends in 2d 04:13:22"; before that, a calm "Offer ends Nov 30".
  - Calls `onExpire` once at zero, so the page refetches server prices (the server stays the source of truth).
  - Render the day count in its own span, outside the LTR time span. Otherwise an Arabic unit letter reorders the digits ("202:55:35 ي").
- **Featured card**: a dark gradient. If the stack uses class-based dark mode, add the `dark` class to the card so nested components theme themselves.
- **Wrapping**: if cards are `<button>`s and the app sets `button { white-space: nowrap }` (common), set `white-space: normal` on the note and chip, or they overflow narrow cards.

### Promotion bar
- **Layout:** full width; messages, a sparkle separator (decorative, `aria-hidden`), a countdown, a CTA pill and a dismiss ✕.
- **Messages:**
  - A continuous marquee **only when the messages overflow**, using a duplicated track. It runs in reverse in RTL and pauses on hover or focus.
  - Under `prefers-reduced-motion`, a static rotation every 5 s instead.
  - When the messages fit, a static row.
- **Countdown:**
  - `02d : 04h : 33m : 12s`, LTR-isolated, tabular numbers.
  - Uses the server offset: `remaining = ends_at − (device_now + (server_time − fetch_time))`.
  - Hide the bar once the countdown passes zero, even before the refetch lands.
- **Accessibility:**
  - `role="region"` with a localized label.
  - The ticking countdown is `aria-hidden`; one sr-only sentence carries the messages and the end date, so screen readers aren't spammed.
- **Dismissal:**
  - Stored per viewer under `promo-dismissed:{id}:{updated_at}`, so editing the promotion shows it again.
  - Wrap storage access in try/catch.
- **CTA:**
  - With no explicit URL, use the surface default: public → register; dashboard → billing via SPA navigation; mobile → **the web app origin**.
  - Hide the default CTA when the viewer is already on that page.
- **Placement:**
  - Above the app header, outside the scrolling area.
  - On full-height auth pages, make the page a column (`min-h-screen flex-col`, with the content `flex-1`) so the bar never adds a scrollbar.
  - Skip the payment-in-progress page.
- **Refresh:** refetch on focus, plus every 5 minutes as a polling floor.

### Mobile
- Use the same tokens and behaviour.
- `AnimatedSwitcher` rotation, static when the OS "remove animations" setting is on.
- `Timer.periodic` countdown with the server offset; refetch on resume and every 5 minutes.
- Dismissal saved in shared preferences.
- **The CTA opens the web CRM origin, not the API origin.** Use explicit `WEB_APP_URL` config only (required in CI; never derived from the API host). When it is unset, hide any CTA that needs the web origin rather than guess. Live testing caught the API host (`…:8000/change-plan`) being opened.

### Admin editor
- **Plan editor:**
  - "Sale price": per cycle, regular price next to sale price, with a live −% chip and an inline error when sale ≥ regular.
  - Start and end pickers in local time, stored as UTC, with a timezone hint and a status line.
  - "Card presentation": badge, custom text (EN/AR), featured toggle, offer note (EN/AR), display order.
  - A **live card preview** with Monthly/Yearly and EN/AR toggles.
- **Promotions list:** name with theme dot, status pill, window, audience chips, priority, inline active toggle, and edit/duplicate/delete. Delete confirms.
- **Promotion editor:**
  - A sticky **live bar preview** with EN/AR and reduced-motion toggles.
  - A messages repeater (reorder up/down, remove, max 6).
  - CTA, theme swatches (radio cards), schedule, countdown toggle (disabled without an end), audiences, dismissible/active toggles, priority.
  - Warnings when another live promotion of equal or higher priority overlaps, and when discounted plans end their sale at a different time.
- **Blur validation must only re-validate the field that was blurred.** Validating the whole message list on blur lit up untouched empty rows, and the layout jump swallowed the next click (found in live testing).

## 7. Edge cases checklist
- [ ] Timezones: store UTC; the admin enters local time; viewers see their local time.
- [ ] Device clock skew → server offset; the clock-bearing endpoint is uncacheable.
- [ ] Sale ends between checkout and webhook → the lock applies it.
- [ ] Sale starts mid-session → the regular amount is still accepted.
- [ ] Price changes while a session is pending → a new gateway session, not reuse.
- [ ] A client-sent amount is ignored.
- [ ] Free or trial plan with a sale → rejected; sale ≥ regular → rejected on save and ignored at runtime.
- [ ] A gateway billing in another currency converts from the charged USD.
- [ ] The invoice line shows the list price, then a "Discount" row, then the charged total.
- [ ] Two live promotions → priority wins; an expired one hands over to the next without a reload.
- [ ] Dismissed, then edited → shows again; storage blocked → dismiss for this view only.
- [ ] RTL: reversed marquee, LTR-isolated numbers, logical `end` positioning, mirrored switches.
- [ ] Narrow screens: one rotating message, compact countdown chips.
- [ ] Reduced motion: no marquee and no fades.
- [ ] Deleting or pausing a promotion has no billing impact.

## 8. Test matrix (server)
- **Pricing:** window states (none, scheduled, live, ended, null bounds), each cycle independent, sale ≥ regular ignored, floor percent.
- **Quote:**
  - initial and renewal charge the sale price
  - upgrades ignore the sale
  - the preview exposes the discount
  - the snapshot is written on the Payment row
- **Lock:**
  - a webhook after the sale ended applies
  - the sale amount without a lock after the sale is rejected
  - a legacy payment at the regular price applies during a sale
- **Invoice:** discount fields set, and the PDF shows Subtotal / Discount / Total.
- **Checkout endpoint:** a client amount is ignored; a pending session is not reused after a price change.
- **Plan API:** public shape and order, admin validation (sale ≥ regular, free plan, custom badge needs both locales), clearing a sale, admin `sale_status`.
- **Promotions:**
  - live selection by window, kill switch, priority and audience
  - status labels
  - the public endpoint: anonymous access, no internal fields, `no-store`
  - an unknown audience falls back
  - admin permission (403 for non-managers)
  - CRUD, duplicate (paused), the status filter
  - every validation rule
- **Validation catalog:** the sample vectors still round-trip.

## 9. Rollout
1. Backend: migrations (schema + badge seed), then export validation snapshots to the clients.
2. Admin panel.
3. Web app.
4. Mobile.

Every API change is additive, so each step is safe alone.

---

### Reference implementation (LOOP CRM)

| Concern | Path |
|---|---|
| Models / migrations | `CRM-api-1/subscriptions/models.py` (`Plan` sale + card fields, `Payment.pricing`, `Invoice.regular_amount/discount_amount`, `Promotion`), `subscriptions/migrations/0032_*`, `0033_seed_most_popular_badge.py` |
| Pricing engine | `CRM-api-1/subscriptions/services/pricing.py` |
| Billing / lock | `CRM-api-1/subscriptions/services/billing.py` (`resolve_checkout_quote`, `_acceptable_full_period_amounts`), `views/checkout.py` |
| Invoices | `subscriptions/invoicing/snapshot.py`, `view_model.py`, `pdf.py`, `templates/subscriptions/invoice_email_*.html` |
| API | `subscriptions/serializers.py` (`PublicPlanSerializer`, `PromotionSerializer`), `subscriptions/views/promotions.py` |
| Validation | `validation/schemas/subscriptions.py` (`plan.upsert`, `promotion.upsert`) |
| Tests | `tests/test_plan_pricing.py`, `tests/test_promotions.py` |
| Admin | `CRM-admin-panel/components/PlanModal.tsx`, `components/pricing/*`, `components/PromotionsTab.tsx`, `components/PromotionModal.tsx` |
| Web | `CRM-project/components/pricing/*`, `hooks/useCountdown.ts`, `hooks/useQueries.ts` (`useActivePromotion`) |
| Mobile | `crm_mobile/lib/widgets/promo_bar.dart`, `lib/services/promotion_service.dart`, `lib/models/promotion_model.dart` |
