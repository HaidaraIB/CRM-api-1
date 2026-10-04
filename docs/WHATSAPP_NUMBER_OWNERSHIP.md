# WhatsApp numbers: one number, one owner

This document explains:
- why some customers' WhatsApp replies weren't showing in the CRM,
- the rule the system now follows,
- the one-time cleanup that brings old data in line with the rule,
- exactly what clients will see.

---

## 1. What went wrong

When a WhatsApp message arrives, Meta tells us only two things:

1. **which of our numbers received it** (the number id, `phone_number_id`), and
2. **who sent it** (the customer's phone).

We look up who owns that number, and the message goes there. A number can be owned by three kinds of "place":

| Owner | Used for | Replies show up in |
|---|---|---|
| **Platform number** | LOOP's own number: signup codes (OTP) and the admin panel ↔ company-owner chat | Admin panel → Company WhatsApp |
| **Company CRM number** | A company's WhatsApp in the CRM | CRM → Chats / lead timeline |
| **Company inbox number** | A company's WhatsApp in the Omni-Channel Inbox | CRM → Inbox |

**The bug:** company 31 had connected **the platform number** as its CRM number. Sending worked. But every reply was treated as "a message to the platform number":

- If the customer happened to be some company's owner, the reply went to **that company's admin-panel thread** (company 35).
- Otherwise it was **thrown away**: "no tenant owner matched" in the log.

Nothing prevented this. Other mix-ups were possible too, each broken in its own way:

| Mix-up | What happened before |
|---|---|
| Platform = company CRM number | Replies diverted to the admin panel or lost **(the reported bug)** |
| Platform = company inbox number | Same |
| Company A CRM = Company B CRM | The number silently **moved** from A to B, and could flip back |
| Company A inbox = Company B inbox | Same silent move |
| Company A CRM = Company B **inbox** | A's customer replies and delivery ticks went into **B's** inbox |
| Same company: CRM = inbox | Already blocked |

Also, when a connection was refused, the screen still said **"Connected successfully"**. The error was never shown.

---

## 2. The rule

> **Every WhatsApp number belongs to exactly one place: the platform, one company's CRM, or one company's inbox.**

There are no exceptions and no "shared number" special cases in the code. The rule is checked everywhere a number can be attached:

| Where | What happens |
|---|---|
| A company connects WhatsApp (CRM or inbox) | Refused if the number belongs to anyone else |
| Background syncs (token refresh, "refresh phone numbers") | Skip a number that belongs to someone else; never move it |
| Admin panel → System Settings → Platform WhatsApp | Refused if a company is using that number |
| Incoming messages and delivery ticks | Go to the one owner |

### Connecting a number: every case

| The number is currently… | Result |
|---|---|
| Not used anywhere | ✅ Connected |
| Already this company's, same place (reconnect) | ✅ Connected |
| Disconnected by another company (number moved to a new business) | ✅ Connected |
| The platform number | ❌ Reserved |
| Connected by another company | ❌ Belongs to another account |
| This company's inbox, being added as CRM (or the reverse) | ❌ Already used for the other one |

When a connection is refused:
- The user sees a clear message in English or Arabic (section 5).
- **Nothing is taken from the other owner.**
- If the company already had a working WhatsApp connection, it **stays connected**. Only the failed attempt is discarded.

---

## 3. Where replies go

Each number has one owner, so this is simple:

- Platform number → admin panel thread (only for company owners, as before).
- Company CRM number → that company's Chats / lead.
- Company inbox number → that company's Inbox.

Delivery ticks (sent / delivered / read / failed) follow the same owner.

If a shared number somehow still exists (for example, before the cleanup in section 4 is run), the system does **not** guess. It sends everything to the owner the cleanup would keep, and writes an **ERROR** to the log telling you to run the cleanup.

---

## 4. One-time cleanup of existing shared numbers

Numbers that were already shared before the rule are fixed **once** by a command. After that, the rule keeps it from ever happening again.

```sh
python manage.py resolve_whatsapp_number_conflicts           # preview only, changes nothing
python manage.py resolve_whatsapp_number_conflicts --apply   # do it
```

### Who keeps a shared number

1. **The platform always keeps the platform number.**
2. Otherwise, **whoever connected the number first** keeps it.
3. You can override a single number: `--keep <phone_number_id>=crm` or `=inbox`.
   - The command refuses to give the platform number to a company. To do that, first change the Platform WhatsApp number in the admin panel, then run the command again.

### What happens to the one that loses the number

- Its WhatsApp connection is **disconnected**: token removed, status "disconnected".
- **All of its messages and leads are kept.** Nothing is deleted.
- Its integration card in the CRM shows **Disconnected**, with a red line explaining why.
- The **company owner** gets an in-app notification and a phone push, in their language (section 5).
- The action is recorded in the integration's log.

Running the command again does nothing once everything is clean. It's safe to repeat.

### For today's data

The only known case is **company 31**: its CRM WhatsApp uses the platform number. With `--apply`:
- the platform keeps the number (signup codes and the admin chat keep working),
- company 31's CRM WhatsApp is disconnected and its owner is told to connect a different number,
- company 31's existing chats stay where they are.

Run the preview first. It lists every shared number it finds and what it will do.

---

## 5. What clients see

Clients don't need to know any of the details above. They only see these messages:

### When a connection is refused (popup on the Integrations page)

| Situation | English | Arabic |
|---|---|---|
| Reserved number | This WhatsApp number is reserved and can't be connected. Please connect a different number. | رقم واتساب هذا محجوز ولا يمكن ربطه. يرجى ربط رقم مختلف. |
| Number belongs to another account | This WhatsApp number is already connected to another account. Disconnect it there first, or contact support. | رقم واتساب هذا مربوط بحساب آخر. افصله من هناك أولاً، أو تواصل مع الدعم. |
| Their inbox number added as CRM | This number is already used for your WhatsApp inbox. Choose a different number for CRM WhatsApp. | هذا الرقم مستخدم لصندوق وارد واتساب. اختر رقماً مختلفاً لواتساب CRM. |
| Their CRM number added as inbox | This number is already used for CRM WhatsApp. Choose a different number for the inbox. | هذا الرقم مستخدم لواتساب CRM. اختر رقماً مختلفاً لصندوق الوارد. |

The same sentence also appears in red under the disconnected WhatsApp card.

### When the cleanup disconnects them (notification to the company owner)

| | English | Arabic |
|---|---|---|
| Title | Your CRM WhatsApp number was disconnected *(or "WhatsApp inbox")* | تم فصل رقم واتساب CRM *(أو "صندوق وارد واتساب")* |
| Text | The number +964… is used elsewhere — each WhatsApp number can be connected in one place only. Connect a different number from Integrations → WhatsApp. Your previous conversations are kept. | تم فصل الرقم ‎+964… لأنه مستخدم في مكان آخر — يمكن ربط كل رقم واتساب في مكان واحد فقط. اربط رقماً مختلفاً من التكاملات ← واتساب. محادثاتك السابقة محفوظة. |

**What they need to do:** go to Integrations → WhatsApp and connect a different number. When they do, the red message disappears.

Everyone else notices nothing.

---

## 6. For you (operator)

**Check that the system is clean:**

```sh
python manage.py whatsapp_debug_check
```

The top of the output says either `Shared WhatsApp numbers: none` or lists them, with the cleanup command to run.

**Logs:**

| Log line | Meaning |
|---|---|
| `ERROR … has several owners … Run: resolve_whatsapp_number_conflicts --apply` | A shared number exists. Run the cleanup. |
| `WhatsApp connect rejected account_id=… key=…` | Someone tried to connect a number they're not allowed to |
| `Skipping WhatsApp sync … conflict=…` | A background sync refused to take a number from its owner |

**Deploy checklist:**
1. Deploy backend + CRM-project together. The frontend has the new messages and the red reason line.
2. Run `python manage.py resolve_whatsapp_number_conflicts` (preview) and read the output.
3. Run it again with `--apply`.
4. `python manage.py whatsapp_debug_check` should show "none".

No database migration is needed.

---

## 7. Known limits

- Replies lost before this fix ("no tenant owner matched" in the logs) were never stored and can't be recovered. Replies that went to company 35's admin-panel thread are still there.

---

## 8. What changed in the code

| File | Change |
|---|---|
| `integrations/services/whatsapp_number_ownership.py` | **New.** The rule in one place: who owns a number, whether a company may connect it, who keeps a shared number, conflict listing. |
| `integrations/management/commands/resolve_whatsapp_number_conflicts.py` | **New.** One-time cleanup (preview / `--apply` / `--keep`), with owner notifications. |
| `integrations/whatsapp_webhook.py` | Incoming messages and delivery ticks go to the number's single owner. |
| `integrations/services/whatsapp_inbox_numbers.py` | Inbox connect uses the rule. The old error class name is kept as an alias for existing imports. |
| `integrations/whatsapp_account_sync.py` | CRM connect and background syncs use the rule and never take a number. |
| `integrations/views/viewsets_accounts.py` | A refused connection returns a real, translatable error and leaves a working connection untouched. |
| `settings/serializers.py` | The Platform WhatsApp number can't be set to a number a company uses. |
| `integrations/management/commands/whatsapp_debug_check.py` | Lists shared numbers. |
| `CRM-project/constants.ts` | English + Arabic messages. |
| `CRM-project/pages/IntegrationsPage.tsx` | Red reason line under a disconnected WhatsApp card. |
| `integrations/tests/test_whatsapp_platform_routing.py` | 21 tests: routing, every connect case, the cleanup, the connect endpoint, platform settings. |
