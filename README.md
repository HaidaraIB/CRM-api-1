# CRM-api-1

CRM-api-1 is a REST API backend built with Django for a multi-tenant (SaaS) CRM application. It powers client and deal management, subscriptions, payment gateways, and integrations with Meta, WhatsApp, and TikTok. The API is consumed by a web frontend (CRM-project) and an admin panel (CRM-admin-panel), and is designed to support domains such as loop-crm.app.

## Key Features

*   **Companies & Users**: Multi-tenant company registration, JWT authentication (SimpleJWT), email verification, password reset, two-factor authentication (2FA), and FCM token updates for push notifications. Role-based access with Limited Admins and Supervisors.
*   **CRM Core**: Clients, deals, tasks, campaigns, client calls, and client events. Configurable lead stages, lead statuses, channels, and call methods. System backups and audit logs.
*   **Real Estate**: Developers, projects, units, and owners for property management.
*   **Services & Products**: Services, service packages, and service providers; products, product categories, and suppliers.
*   **Subscriptions & Payments**: Plans, subscriptions, invoices, and multiple payment gateways (PayTabs, Stripe, ZainCash, QiCard, FIB). Scheduled broadcasts for subscription-related messaging.
*   **Integrations**: Meta (Facebook/Instagram Lead Forms), WhatsApp, and TikTok (OAuth and Lead Gen). Webhooks for leads and messages; each tenant can connect their own accounts. See [docs/INTEGRATIONS_COMPLETE_GUIDE.md](docs/INTEGRATIONS_COMPLETE_GUIDE.md) for setup.
*   **Notifications**: Firebase and Twilio for push and SMS. Reminders for leads, tasks, calls, deals, and subscriptions; daily and weekly reports; top-employee notifications. Many tasks run via management commands and cron (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [crontab_complete.txt](crontab_complete.txt)).
*   **Admin & Support**: Super Admin impersonation (login as a company owner) for support and debugging. See [docs/IMPERSONATION.md](docs/IMPERSONATION.md).
*   **API Documentation**: Interactive Swagger UI at `/api/docs/` and ReDoc at `/api/redoc/` (drf-spectacular).

## Technology Stack & Architecture

The backend is built with Django and Django REST Framework in a multi-app structure.

*   **Framework**: Django 5.x, Django REST Framework (DRF)
*   **Authentication**: `djangorestframework-simplejwt` for JWT access/refresh tokens, with 2FA support
*   **Database**: SQLite by default (upgradable to PostgreSQL or MySQL for production; see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md))
*   **CORS**: `django-cors-headers` for cross-origin requests from frontend and admin panel
*   **API Documentation**: `drf-spectacular` and `drf-spectacular-sidecar` for OpenAPI schema, Swagger, and ReDoc
*   **Background Tasks**: `django-q2` for in-process task queue; optional Celery and Redis for heavier workloads
*   **Payments**: Stripe and other gateways (PayTabs, ZainCash, QiCard, FIB) with webhook/callback handling
*   **Integrations**: OAuth and webhooks for Meta, WhatsApp, and TikTok; `cryptography` for storing tokens
*   **Notifications**: Firebase Admin SDK for push, Twilio for SMS
*   **Other**: `Pillow` for images, `django-ratelimit` for rate limiting, `python-dotenv` for environment variables

## Project Structure

The codebase is organized into Django apps and a project package for settings and URL routing.

```
CRM-api-1/
├── crm_saas_api/    # Project package: settings, urls, wsgi, middleware
├── accounts/        # Users, auth (register, login, 2FA, FCM), impersonation
├── companies/       # Multi-tenant companies and registration
├── crm/             # Clients, deals, tasks, campaigns, calls, events
├── real_estate/     # Developers, projects, units, owners
├── services/        # Services, packages, providers
├── products/        # Products, categories, suppliers
├── subscriptions/   # Plans, subscriptions, payments, invoices, gateways, broadcasts
├── settings/        # Channels, stages, statuses, call methods, backups, audit log, system settings
├── integrations/    # Meta, WhatsApp, TikTok OAuth and webhooks
├── notifications/   # Notification endpoints and logic (Firebase, Twilio, reminders)
├── docs/            # Deployment, integrations, impersonation, and other guides
├── manage.py
├── requirements.txt
└── crontab_complete.txt   # Template for cron jobs (subscriptions, notifications, etc.)
```

## Setup and Installation

To run the API locally:

1.  **Clone the repository:**
    ```sh
    git clone <repository-url> CRM-api-1
    cd CRM-api-1
    ```

2.  **Create a virtual environment and install dependencies:**
    ```sh
    python -m venv venv
    source venv/bin/activate   # On Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

3.  **Create a `.env` file:**
    Create a file named `.env` in the project root. At minimum, set:
    ```
    SECRET_KEY=your-django-secret-key
    DEBUG=True
    BASE_DOMAIN=
    ```
    For production, CORS, database URL, and API keys for payment gateways and integrations (Meta, TikTok, etc.) are required. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/INTEGRATIONS_COMPLETE_GUIDE.md](docs/INTEGRATIONS_COMPLETE_GUIDE.md) for the full list.

4.  **Run migrations:**
    ```sh
    python manage.py migrate
    ```

5.  **Run the development server:**
    ```sh
    python manage.py runserver
    ```
    For production, use Gunicorn behind Nginx as described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

6.  **API docs:** Once the server is running, open [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/) for Swagger or [http://localhost:8000/api/redoc/](http://localhost:8000/api/redoc/) for ReDoc.

## Documentation

*   **Deployment (VPS, Gunicorn, Nginx, SSL, cron):** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
*   **Integrations (Meta, WhatsApp, TikTok):** [docs/INTEGRATIONS_COMPLETE_GUIDE.md](docs/INTEGRATIONS_COMPLETE_GUIDE.md)
*   **Impersonation (Super Admin):** [docs/IMPERSONATION.md](docs/IMPERSONATION.md)
*   **Cron jobs template:** [crontab_complete.txt](crontab_complete.txt) (scheduled broadcasts, subscription reminders, notifications, etc.)
