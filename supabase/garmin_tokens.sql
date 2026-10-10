-- Private table for the Garmin Bridge's encrypted Garmin session tokens.
-- Run once in Supabase: SQL Editor -> New query -> paste -> Run.
-- Only the backend (secret key / service_role) can read or write it.

create table if not exists public.garmin_tokens (
  id text primary key,
  token_ciphertext text not null,  -- AES-256-GCM encrypted by the bridge; never plaintext
  updated_at timestamptz not null default now()
);

-- RLS on with no policies: the public (publishable/anon) key and logged-in
-- users get no access at all. The secret key bypasses RLS.
alter table public.garmin_tokens enable row level security;

revoke all on table public.garmin_tokens from anon, authenticated;
grant select, insert, update on table public.garmin_tokens to service_role;
