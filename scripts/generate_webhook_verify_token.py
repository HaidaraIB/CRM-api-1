#!/usr/bin/env python3
"""
Generate a strong random string for Meta/WhatsApp webhook verify token.

Usage:
  .venv\\Scripts\\python.exe scripts/generate_webhook_verify_token.py
  .venv\\Scripts\\python.exe scripts/generate_webhook_verify_token.py --bytes 32

Set the output as WHATSAPP_WEBHOOK_VERIFY_TOKEN (or META_WEBHOOK_VERIFY_TOKEN) in your API env.
Must match exactly what you enter in Meta Developer -> Webhooks -> Verify token.
"""
from __future__ import annotations

import argparse
import secrets


def main() -> None:
    p = argparse.ArgumentParser(description="Generate a webhook verify token for Meta/WhatsApp.")
    p.add_argument(
        "--bytes",
        type=int,
        default=32,
        metavar="N",
        help="Number of random bytes (default: 32). Token length in hex will be 2*N characters.",
    )
    args = p.parse_args()
    if args.bytes < 16:
        p.error("--bytes should be at least 16 for a reasonable token strength.")

    # URL-safe: alphanumeric + - and _ ; works well in .env and Meta UI without quoting issues.
    token = secrets.token_urlsafe(args.bytes)
    print(token)


if __name__ == "__main__":
    main()
