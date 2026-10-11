-- Tables used by the ProcureGuard n8n workflows (PostgreSQL)
CREATE TABLE ap_events (id serial primary key, event_id text, event text, severity text, team text, about text,
  subject text, payload text, occurred_at text, logged_at timestamptz default now());
CREATE TABLE msme_digest (id serial primary key, business_date text, msme_due_7d int, discounts_open int,
  overdue int, non_po int, body text, created_at timestamptz default now());
CREATE TABLE callback_verifications (id serial primary key, vendor_id text, phone_called text, phone_on_file text,
  person_spoken_to text, vendor_confirmed text, result text, reasons text, verified_by text,
  created_at timestamptz default now());
