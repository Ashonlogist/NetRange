-- NetRange shared scans table.
-- Run this once in your Supabase project (SQL Editor -> New query -> Run).

create table if not exists scans (
    id              bigint generated always as identity primary key,
    ssid            text,
    bssid           text,
    signal_dbm      double precision,
    signal_pct      integer,
    strength_raw    double precision,
    channel         text,
    frequency       integer,
    lat             double precision,
    lon             double precision,
    accuracy        double precision,
    device_id       text,
    source          text default 'mobile',
    client_timestamp timestamptz,          -- when the phone took the scan
    created_at      timestamptz not null default now()  -- when the server received it
);

-- Coverage queries always filter by SSID and want the newest rows first.
create index if not exists scans_ssid_idx on scans (ssid);
create index if not exists scans_created_at_idx on scans (created_at desc);

-- Row Level Security: the backend talks to Supabase with the service_role
-- key, which bypasses RLS entirely, so this table can stay locked down to
-- everyone else by default. Enable RLS with no policies = nobody using the
-- anon/public key can read or write this table directly.
alter table scans enable row level security;
