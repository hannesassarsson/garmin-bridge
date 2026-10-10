-- Daily Garmin health data synced by the bridge (one row per calendar day).
-- Only the backend (secret key / service_role) can read or write via the API.

create table if not exists public.daily_health (
  day date primary key,
  sleep jsonb,       -- get_sleep_data: dailySleepDTO, sleep levels, overnight HRV, ...
  hrv jsonb,         -- get_hrv_data: hrvSummary (last night, weekly average, status, baseline)
  summary jsonb,     -- get_user_summary: resting HR, Body Battery, stress, steps, ...
  readiness jsonb,   -- get_training_readiness: list of readiness scores for the day
  synced_at timestamptz not null default now()
);

alter table public.daily_health enable row level security;
revoke all on table public.daily_health from anon, authenticated;
grant select, insert, update on table public.daily_health to service_role;

-- One row per day with the key recovery metrics as plain columns.
create or replace view public.health_overview with (security_invoker = true) as
select
  day,
  (sleep->'dailySleepDTO'->'sleepScores'->'overall'->>'value')::numeric              as sleep_score,
  sleep->'dailySleepDTO'->'sleepScores'->'overall'->>'qualifierKey'                   as sleep_quality,
  round((sleep->'dailySleepDTO'->>'sleepTimeSeconds')::numeric / 3600, 2)             as sleep_hours,
  round((sleep->'dailySleepDTO'->>'deepSleepSeconds')::numeric / 60)                  as deep_min,
  round((sleep->'dailySleepDTO'->>'lightSleepSeconds')::numeric / 60)                 as light_min,
  round((sleep->'dailySleepDTO'->>'remSleepSeconds')::numeric / 60)                   as rem_min,
  round((sleep->'dailySleepDTO'->>'awakeSleepSeconds')::numeric / 60)                 as awake_min,
  to_timestamp((sleep->'dailySleepDTO'->>'sleepStartTimestampLocal')::numeric / 1000)
    at time zone 'UTC'                                                                 as sleep_start_local,
  to_timestamp((sleep->'dailySleepDTO'->>'sleepEndTimestampLocal')::numeric / 1000)
    at time zone 'UTC'                                                                 as sleep_end_local,
  round((sleep->'dailySleepDTO'->>'avgSleepStress')::numeric, 1)                      as sleep_stress,
  round((sleep->'dailySleepDTO'->>'averageRespirationValue')::numeric, 1)             as sleep_respiration,
  (hrv->'hrvSummary'->>'lastNightAvg')::numeric                                       as hrv_last_night,
  (hrv->'hrvSummary'->>'lastNight5MinHigh')::numeric                                  as hrv_5min_high,
  (hrv->'hrvSummary'->>'weeklyAvg')::numeric                                          as hrv_weekly_avg,
  hrv->'hrvSummary'->>'status'                                                        as hrv_status,
  (hrv->'hrvSummary'->'baseline'->>'balancedLow')::numeric                            as hrv_baseline_low,
  (hrv->'hrvSummary'->'baseline'->>'balancedUpper')::numeric                          as hrv_baseline_high,
  (summary->>'restingHeartRate')::numeric                                             as resting_hr,
  (summary->>'lastSevenDaysAvgRestingHeartRate')::numeric                             as resting_hr_7d_avg,
  (summary->>'bodyBatteryHighestValue')::numeric                                      as body_battery_high,
  (summary->>'bodyBatteryLowestValue')::numeric                                       as body_battery_low,
  (summary->>'bodyBatteryChargedValue')::numeric                                      as body_battery_charged,
  (summary->>'bodyBatteryDrainedValue')::numeric                                      as body_battery_drained,
  (summary->>'averageStressLevel')::numeric                                           as avg_stress,
  (summary->>'totalSteps')::numeric                                                   as steps,
  (summary->>'activeKilocalories')::numeric                                           as active_kcal,
  (summary->>'moderateIntensityMinutes')::numeric                                     as moderate_intensity_min,
  (summary->>'vigorousIntensityMinutes')::numeric                                     as vigorous_intensity_min,
  (readiness->0->>'score')::numeric                                                   as training_readiness,
  readiness->0->>'level'                                                              as training_readiness_level
from public.daily_health;

revoke all on public.health_overview from anon, authenticated;
grant select on public.health_overview to service_role;
