import os

bind = "127.0.0.1:8000"

# Threaded workers, not sync.
#
# This process does a lot of blocking outbound I/O inside the request cycle —
# Meta Graph (WhatsApp media upload uses timeout=60), FCM, Twilio/OTPIQ, the
# payment gateways, Resend. With sync workers, each of those calls occupies a
# whole worker, so a handful of slow Graph calls could stall the API for everyone.
# gthread lets a blocked call hold a thread instead of a whole request slot.
#
# Sized explicitly rather than derived from cpu_count(), for two reasons:
#   - cpu_count() reports the guest's logical CPUs, which on a VPS need not match
#     the vCPU the plan actually bills, so the process count could change silently
#     under the host's feet.
#   - this box is shared. Another Django app (point_digital_marketing_manager_api)
#     runs its own gunicorn here, plus Postgres, Redis and nginx, so the CRM does
#     not get all the cores and should not size itself as though it does.
#
# 2 x 4 = 8 concurrent requests. Raise GUNICORN_WORKERS only after confirming
# spare CPU — past the real core count, more workers add contention, not capacity.
# The threads absorb I/O waiting; they do not add CPU throughput (GIL).
worker_class = "gthread"
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))

# Above the longest outbound timeout in the codebase (whatsapp_media, 60s) so a
# slow upstream returns an error from our own code rather than having the worker
# killed mid-request.
timeout = 90
graceful_timeout = 30
keepalive = 2

max_requests = 1000
max_requests_jitter = 50
preload_app = True

# Request time (%(D)s, microseconds) is in the access log so the latency knee is
# measurable before it becomes a user complaint.
accesslog = "-"
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)s'
