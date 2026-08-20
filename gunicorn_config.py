import multiprocessing

bind = "127.0.0.1:8000"

# Threaded workers, not sync.
#
# This process does a lot of blocking outbound I/O inside the request cycle —
# Meta Graph (WhatsApp media upload uses timeout=60), FCM, Twilio/OTPIQ, the
# payment gateways, Resend. With sync workers, each of those calls occupies a
# whole worker, so on a 2-vCPU box a handful of slow Graph calls could stall the
# API for everyone. gthread lets a blocked call hold a thread instead of a slot.
#
# 3 workers x 4 threads = 12 concurrent requests on 2 vCPU. Workers are kept at
# roughly the core count because the work is still GIL-bound Python once the I/O
# returns; the threads are there to absorb waiting, not to add CPU throughput.
worker_class = "gthread"
workers = max(2, multiprocessing.cpu_count())
threads = 4

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
