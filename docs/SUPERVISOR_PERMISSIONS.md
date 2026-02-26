# Supervisor logical permissions (API)

This document defines how **supervisor permissions** map to API access and to UI sections in CRM-project and crm_mobile. Permissions are **feature-based**, not per-table.

## Leads vs Clients (in this CRM)

- **In the database**: There is no separate "Lead" model. Both **Leads** and **Clients** refer to the same entity: the **Client** model. One record = one client/lead.
- **In the UI and permissions**: **Leads** = access to the "Leads" section (pipeline/list) and **Activities** (client-tasks, client-calls). There is no separate "Clients" permission; client/lead data is controlled by the Leads permission only.

## Permission flags (backend)

| Flag | Meaning |
|------|--------|
| `can_manage_leads` | Leads + Activities (clients/leads CRUD, client-tasks, client-calls, read-only settings for filters) |
| `can_manage_deals` | Deals CRUD |
| `can_manage_tasks` | Tasks CRUD (deal tasks) |
| `can_view_reports` | Reports / analytics |
| `can_manage_users` | Users CRUD (company users) |
| `can_manage_products` | Products, categories, suppliers |
| `can_manage_services` | Services, service packages, service providers |
| `can_manage_real_estate` | Developers, projects, units, owners |
| `can_manage_settings` | Full settings (channels, stages, statuses, call-methods: read/write) |

**Inventory permissions (one per company):** Only the permission that matches the **company specialization** can be granted. A real estate company cannot grant Products or Services; a products company cannot grant Real Estate or Services; a services company cannot grant Real Estate or Products.

| Company specialization | Allowed inventory permission |
|------------------------|-----------------------------|
| `real_estate`          | `can_manage_real_estate` only (Properties, Owners) |
| `products`             | `can_manage_products` only (Products, Categories, Suppliers) |
| `services`             | `can_manage_services` only (Services, Packages, Service Providers) |

## API access matrix

- **Admin**: full access to company data (unchanged).
- **Employee**: access as defined per view (often read-only).
- **Supervisor**: access only when the corresponding permission is granted.

### Leads / Activities

| Resource | can_manage_leads | can_manage_settings |
|----------|------------------|---------------------|
| Leads / Clients (CRM) | full (company filter) | — |
| client-tasks | full (company filter) | — |
| client-calls | full (company filter) | — |
| settings/channels | **read-only** | — | full |
| settings/stages | **read-only** | — | full |
| settings/statuses | **read-only** | — | full |
| settings/call-methods | **read-only** | — | full |

So: a supervisor with **only** `can_manage_leads` can open Leads and Activities, use client-tasks and client-calls, and **read** channels, stages, statuses, and call-methods (e.g. for filters). They cannot create/update/delete those settings.

### Users

- **Users list**: Supervisor with `can_manage_leads` can **list** company users (for Activities filter). Supervisor with `can_manage_users` can list and CRUD company users.
- **users/me**: All authenticated users.

### Other resources

- **Deals**: `can_manage_deals` → full (company filter).
- **Tasks** (deal tasks): `can_manage_tasks` → full (company filter).
- **Products / categories / suppliers**: `can_manage_products`.
- **Services / service packages / service providers**: `can_manage_services`.
- **Real estate** (developers, projects, units, owners): `can_manage_real_estate`.

## Frontend / mobile usage

- **CRM-project** and **crm_mobile** should show **tabs/pages** only when the user has the matching permission (e.g. show Leads + Activities when `can_manage_leads`).
- **Do not call** APIs for modules the user has no permission for (e.g. do not call `/api/deals/` when the user has only `can_manage_leads`), or handle 403 and hide/disable the corresponding UI.
- **users/me** is allowed for any authenticated user; ensure the token is sent for requests that need it.

## Summary

- **Leads permission** → Leads + Activities UI works: client-tasks, client-calls, and **read-only** access to channels, stages, statuses, call-methods.
- **Settings permission** → Full read/write on those settings.
- All other permissions align with the feature name (clients, deals, tasks, users, products, services, real estate).
