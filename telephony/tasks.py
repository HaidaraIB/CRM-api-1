"""django-q entry points for the dedicated ``telephony`` cluster (see settings.Q_CLUSTER)."""

from __future__ import annotations


def run_provisioning_job(job_id: str) -> None:
    from telephony.services.provisioning import run_job

    run_job(job_id)
