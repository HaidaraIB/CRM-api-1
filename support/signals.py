"""
Signals for support-related events.

Note: Support ticket confirmation email is sent from the view (SupportTicketViewSet.perform_create)
so we have access to the request and can use the X-Language header (current UI language).
"""
