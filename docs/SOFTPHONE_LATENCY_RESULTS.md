# Softphone latency validation results

Record real-device measurements after Phases 4–6. Correlate backend logs (`softphone_ringing`, `softphone_push_dispatch`, `softphone_push_result`) with mobile debug logs (`[softphone_timing]`).

## Environment

| Item | Value |
|------|-------|
| PBX | `shatalarab.uae.zycoo.com` |
| CRM API build | _pending_ |
| Mobile build | _pending_ |
| PBX no-answer timeout (sec) | _measure on PBX_ |
| APNS VoIP configured | _yes/no_ |
| TURN reachable from cellular | _yes/no_ |

## Raw call timelines (10 killed-app tests)

| # | Platform | Network | RINGING→push (ms) | push→CallKit (ms) | CallKit→REGISTER (ms) | REGISTER→200 (ms) | 200→INVITE (ms) | INVITE→answer (ms) | answer→audio (ms) | Total wake→answer (ms) | Pass |
|---|----------|---------|-------------------|-------------------|----------------------|-------------------|-----------------|-------------------|-------------------|------------------------|------|
| 1 | | | | | | | | | | | |
| 2 | | | | | | | | | | | |
| 3 | | | | | | | | | | | |
| 4 | | | | | | | | | | | |
| 5 | | | | | | | | | | | |
| 6 | | | | | | | | | | | |
| 7 | | | | | | | | | | | |
| 8 | | | | | | | | | | | |
| 9 | | | | | | | | | | | |
| 10 | | | | | | | | | | | |

## Summary

| Metric | Value (ms) |
|--------|------------|
| p50 wake→answer | _pending_ |
| p95 wake→answer | _pending_ |
| PBX no-answer timeout | _pending_ |
| Margin (timeout − p95) | _pending_ |

## Phase 4 — PBX operational checklist

- [ ] 4.1 Intrusion/geo-IP on Remote Access proxy enabled (screenshot/export attached)
- [ ] 4.2 Ring timeout ≥ p95 wake→answer (or ring group with longer retry)
- [ ] 4.3 STUN + TURN populated and reachable from cellular
- [ ] 4.4 CooCall disabled on all LOOP mobile extensions

## Phase 6 — Test playbook log

### Stage 0–2

| Step | Result | Notes |
|------|--------|-------|
| 0.x | _pending_ | |
| 1.x | _pending_ | |
| 2.x | _pending_ | |

### Stage 3 (killed app)

| Step | Result | Notes |
|------|--------|-------|
| 3.x iOS | _pending_ | |
| 3.x Android Samsung/Xiaomi | _pending_ | `adb shell dumpsys package com.loopcrm.mobile \| grep stopped` before/after |

### Stage 4 regression

| Step | Result | Notes |
|------|--------|-------|
| 4.x AMI click-to-dial | _pending_ | |
| 4.x Screen pop | _pending_ | |

### Security & resilience (6.5–6.6)

| Test | Result | Notes |
|------|--------|-------|
| 6.5 `sip_password` only in config API | _pending_ | |
| 6.6 Wi-Fi↔cellular mid-call ICE restart | _pending_ | |

## Decision

_pending — extend PBX timeout / optimize cold start / document accepted miss rate._
