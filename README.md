# HubSpot Rep Dashboard

Local web tool that pulls SMB Team contacts matching 21 lead sources from HubSpot,
groups them per rep, and shows calls, emails, connected, and opportunities.

## First-time setup

1. Open `.env.example`, copy it to a new file named `.env`, and paste your
   HubSpot Private App token (`pat-na1-...`).

   The token needs these scopes:
   - `crm.objects.contacts.read`
   - `crm.objects.deals.read`
   - `crm.objects.owners.read`
   - `sales-email-read`
   - `crm.schemas.contacts.read`

2. Double-click `run.bat`. The first run installs Flask/requests into a
   `.venv` folder and opens `http://localhost:5050` in your browser.

   (Port 5050 — your `MBR_Dashboard` tool already owns 5000.)

## Using it

- **Refresh data** — pulls the full contact set from HubSpot. First run can
  take 1–3 minutes if you have thousands of qualifying contacts; subsequent
  refreshes are quicker.
- **Date picker** — when set, calls / emails / connected / opportunities are
  limited to activity that happened ON that day. The Contacts column always
  shows the rep's full pool of qualifying contacts.
- **See opportunities** — opens a side panel listing each deal created from
  these contacts (rep, deal name, amount, stage, close date).

## Filters used

- `lead_source` is one of: Contact, Cold Call, Inbound Call, 0365-2022,
  M365 conference leads 2023, Outbound SDR, Linkedin, Google NXT,
  Outbound Source, Sales extension, ZoomInfo, other, Google NXT 2025,
  MO365 2025, Mailchimp Campaign, Outbound Email, CF Manage Zoominfo,
  Apollo & Clay, Marketplace Lead, M365-Con 2026, Google Cloud Next2026
- `hubspot_team_id` = SMB Team

## Definitions

- **Calls** — `calls` engagements associated with these contacts (logged by rep)
- **Emails** — outbound `emails` engagements (`hs_email_direction` = EMAIL or
  FORWARDED_EMAIL) associated with these contacts
- **Connected** — count of contacts where the rep received any incoming email
  reply (`hs_email_direction` = INCOMING_EMAIL) OR a call's disposition is
  Connected (`hs_call_disposition` = `f240bbac-87c9-4f6e-bf70-924b57d47db7`)
- **Opportunities** — deals associated with these contacts
