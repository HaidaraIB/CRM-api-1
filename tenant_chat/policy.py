"""
Tenant internal chat — product policy (locked).

1. data_entry: Same eligibility as employee — may chat only with owner and active supervisors;
   cannot chat with other employees or data_entry users.

2. Supervisor participant: Only supervisors with an existing SupervisorPermission row where
   is_active=True appear in chat participant lists and may send/receive chat as supervisors.
   Supervisors without a permission row or with is_active=False are excluded (cannot_chat).

Authorization logic lives in tenant_chat.authorization; do not use can_manage_users or
can_access_user() for chat eligibility.
"""

DATA_ENTRY_FOLLOWS_EMPLOYEE_CHAT_RULES = True
INACTIVE_SUPERVISOR_EXCLUDED_FROM_CHAT = True
