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
    created_at      timestamptz not null default now(),  -- when the server received it
    download_speed_mbps double precision   -- measured download speed in mb/s
);

-- Coverage queries always filter by SSID and want the newest rows first.
create index if not exists scans_ssid_idx on scans (ssid);
create index if not exists scans_created_at_idx on scans (created_at desc);

-- Row Level Security: the backend talks to Supabase with the service_role
-- key, which bypasses RLS entirely, so this table can stay locked down to
-- everyone else by default. Enable RLS with no policies = nobody using the
-- anon/public key can read or write this table directly.
alter table scans enable row level security;

create table if not exists api_keys (
    id          bigint generated always as identity primary key,
    key         text unique not null,
    customer    text not null,
    daily_limit integer not null default 1000 check (daily_limit > 0),
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);

create table if not exists api_key_usage (
    id         bigint generated always as identity primary key,
    key_id     bigint not null references api_keys(id) on delete cascade,
    day        date not null default current_date,
    hits       integer not null default 1 check (hits >= 0),
    unique(key_id, day)
);

create index if not exists api_key_usage_day_idx on api_key_usage (day);

alter table api_keys enable row level security;
alter table api_key_usage enable row level security;
