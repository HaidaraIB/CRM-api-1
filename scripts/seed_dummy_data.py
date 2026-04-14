from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from companies.models import Company
from crm.models import Client, ClientCall, ClientTask, Deal, Task
from settings.models import CallMethod, Channel, LeadStage, LeadStatus


def seed_company_data(
    *,
    company_id: int,
    user_id: int,
    leads_count: int = 120,
    deals_count: int = 60,
    tasks_count: int = 120,
    client_tasks_count: int = 150,
    client_calls_count: int = 120,
) -> None:
    User = get_user_model()

    company = Company.objects.filter(id=company_id).first()
    if not company:
        raise ValueError(f"Company with id={company_id} was not found.")

    user = User.objects.filter(id=user_id).first()
    if not user:
        raise ValueError(f"User with id={user_id} was not found.")

    if user.company_id != company.id:
        raise ValueError(
            f"User {user_id} is linked to company {user.company_id}, expected {company.id}."
        )

    statuses = list(LeadStatus.objects.filter(company=company, is_active=True))
    channels = list(Channel.objects.filter(company=company, is_active=True))
    stages = list(LeadStage.objects.filter(company=company, is_active=True))
    call_methods = list(CallMethod.objects.filter(company=company, is_active=True))

    now = timezone.now()
    seed_tag = now.strftime("%Y%m%d%H%M%S")

    first_names = [
        "Ali",
        "Sara",
        "Omar",
        "Lina",
        "Noor",
        "Ahmed",
        "Rama",
        "Zain",
        "Maya",
        "Hassan",
    ]
    last_names = [
        "Kareem",
        "Nasser",
        "Jabbar",
        "Samir",
        "Faris",
        "Saad",
        "Hadi",
        "Qasim",
        "Mahdi",
        "Yousef",
    ]

    with transaction.atomic():
        clients_to_create: list[Client] = []
        for i in range(leads_count):
            full_name = f"{random.choice(first_names)} {random.choice(last_names)} #{seed_tag}-{i+1}"
            clients_to_create.append(
                Client(
                    name=full_name,
                    priority=random.choice(["low", "medium", "high"]),
                    type=random.choice(["fresh", "cold"]),
                    communication_way=random.choice(channels) if channels else None,
                    status=random.choice(statuses) if statuses else None,
                    budget=Decimal(str(random.randint(5_000, 120_000))),
                    phone_number=f"+96477{random.randint(10000000, 99999999)}",
                    lead_company_name=f"Dummy Co {random.randint(1, 40)}",
                    company=company,
                    assigned_to=user,
                    assigned_at=now - timedelta(days=random.randint(0, 60)),
                    source="manual",
                )
            )

        created_clients = Client.objects.bulk_create(clients_to_create, batch_size=500)

        chosen_clients_for_deals = random.sample(
            created_clients, k=min(deals_count, len(created_clients))
        )
        deals_to_create: list[Deal] = []
        for client in chosen_clients_for_deals:
            start_date = (now - timedelta(days=random.randint(1, 120))).date()
            maybe_closed = random.choice([True, False])
            deals_to_create.append(
                Deal(
                    client=client,
                    company=company,
                    employee=user,
                    stage=random.choice(
                        ["in_progress", "on_hold", "won", "lost", "cancelled"]
                    ),
                    payment_method=random.choice(["cash", "installment"]),
                    status=random.choice(["reservation", "contracted", "closed"]),
                    value=Decimal(str(random.randint(20_000, 500_000))),
                    start_date=start_date,
                    closed_date=(
                        start_date + timedelta(days=random.randint(7, 90))
                        if maybe_closed
                        else None
                    ),
                    started_by=user,
                    closed_by=user if maybe_closed else None,
                    description="Dummy seeded deal for pagination/testing.",
                )
            )

        created_deals = Deal.objects.bulk_create(deals_to_create, batch_size=500)

        tasks_to_create: list[Task] = []
        for _ in range(tasks_count):
            deal = random.choice(created_deals)
            tasks_to_create.append(
                Task(
                    deal=deal,
                    stage=random.choice(stages) if stages else None,
                    notes="Dummy follow-up task",
                    reminder_date=now + timedelta(days=random.randint(1, 30)),
                )
            )
        Task.objects.bulk_create(tasks_to_create, batch_size=500)

        client_tasks_to_create: list[ClientTask] = []
        for _ in range(client_tasks_count):
            client = random.choice(created_clients)
            client_tasks_to_create.append(
                ClientTask(
                    client=client,
                    stage=random.choice(stages) if stages else None,
                    notes="Dummy client activity note",
                    reminder_date=now + timedelta(days=random.randint(1, 20)),
                    created_by=user,
                )
            )
        ClientTask.objects.bulk_create(client_tasks_to_create, batch_size=500)

        client_calls_to_create: list[ClientCall] = []
        for _ in range(client_calls_count):
            client = random.choice(created_clients)
            call_at = now - timedelta(days=random.randint(0, 45))
            client_calls_to_create.append(
                ClientCall(
                    client=client,
                    call_method=random.choice(call_methods) if call_methods else None,
                    notes="Dummy call log for testing.",
                    call_datetime=call_at,
                    follow_up_date=call_at + timedelta(days=random.randint(1, 14)),
                    created_by=user,
                )
            )
        ClientCall.objects.bulk_create(client_calls_to_create, batch_size=500)

    print("Dummy data seeded successfully.")
    print(f"company_id={company_id}, user_id={user_id}, seed_tag={seed_tag}")
    print(
        f"created: leads={len(created_clients)}, deals={len(created_deals)}, "
        f"tasks={tasks_count}, client_tasks={client_tasks_count}, client_calls={client_calls_count}"
    )


if __name__ == "__main__":
    seed_company_data(company_id=123, user_id=142)
