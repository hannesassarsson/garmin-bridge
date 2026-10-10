-- Garmin activities synced by the bridge. Run once in Supabase SQL Editor.
-- Only the backend (secret key / service_role) can read or write via the API.

create table if not exists public.activities (
  activity_id bigint primary key,
  start_time_gmt timestamptz,
  summary jsonb not null,          -- Garmin activity list entry (all fields)
  details jsonb,                   -- Garmin activity details (summaryDTO etc.)
  splits jsonb,                    -- laps
  synced_at timestamptz not null default now(),
  details_synced_at timestamptz
);

create index if not exists activities_start_time_idx on public.activities (start_time_gmt desc);

alter table public.activities enable row level security;
revoke all on table public.activities from anon, authenticated;
grant select, insert, update on table public.activities to service_role;

-- One row per activity with the most useful fields as plain columns.
create or replace view public.activity_overview with (security_invoker = true) as
select
  activity_id,
  (summary->>'startTimeLocal')::timestamp                           as start_local,
  summary->'activityType'->>'typeKey'                                as sport,
  summary->>'activityName'                                           as name,
  round((summary->>'distance')::numeric / 1000, 2)                   as distance_km,
  round((summary->>'duration')::numeric / 60, 1)                     as duration_min,
  round((summary->>'movingDuration')::numeric / 60, 1)               as moving_min,
  case when (summary->>'distance')::numeric > 0
       then round((summary->>'duration')::numeric / 60 / ((summary->>'distance')::numeric / 1000), 2) end
                                                                     as pace_min_per_km,
  round((summary->>'averageSpeed')::numeric * 3.6, 1)                as avg_speed_kmh,
  (summary->>'averageHR')::numeric                                   as avg_hr,
  (summary->>'maxHR')::numeric                                       as max_hr,
  (summary->>'avgPower')::numeric                                    as avg_power,
  round((summary->>'normPower')::numeric)                            as norm_power,
  (summary->>'maxPower')::numeric                                    as max_power,
  round((summary->>'max20MinPower')::numeric)                        as max_20min_power,
  coalesce(summary->>'averageBikingCadenceInRevPerMinute',
           summary->>'averageRunningCadenceInStepsPerMinute')::numeric as avg_cadence,
  (summary->>'elevationGain')::numeric                               as elevation_gain_m,
  (summary->>'calories')::numeric                                    as calories,
  round((summary->>'activityTrainingLoad')::numeric, 1)              as training_load,
  round((summary->>'aerobicTrainingEffect')::numeric, 1)             as aerobic_te,
  round((summary->>'anaerobicTrainingEffect')::numeric, 1)           as anaerobic_te,
  summary->>'trainingEffectLabel'                                    as training_effect_label,
  round((details->'summaryDTO'->>'trainingStressScore')::numeric, 1) as tss,              -- only if Garmin provides it
  round((details->'summaryDTO'->>'intensityFactor')::numeric, 2)     as intensity_factor, -- only if Garmin provides it
  (summary->>'hrTimeInZone_1')::numeric                              as hr_zone1_s,
  (summary->>'hrTimeInZone_2')::numeric                              as hr_zone2_s,
  (summary->>'hrTimeInZone_3')::numeric                              as hr_zone3_s,
  (summary->>'hrTimeInZone_4')::numeric                              as hr_zone4_s,
  (summary->>'hrTimeInZone_5')::numeric                              as hr_zone5_s,
  jsonb_array_length(coalesce(splits->'lapDTOs', '[]'::jsonb))       as lap_count,
  details_synced_at is not null                                      as has_details,
  (details->'summaryDTO'->>'directWorkoutRpe')::numeric              as rpe,
  (details->'summaryDTO'->>'directWorkoutFeel')::numeric             as feel,
  (details->'summaryDTO'->>'recoveryHeartRate')::numeric             as recovery_hr,
  round((details->'summaryDTO'->>'averageTemperature')::numeric, 1)  as avg_temperature_c,
  round((details->'summaryDTO'->>'avgRespirationRate')::numeric, 1)  as avg_respiration,
  (details->'summaryDTO'->>'differenceBodyBattery')::numeric         as body_battery_change,
  round((details->'summaryDTO'->>'strideLength')::numeric)           as stride_length_cm,
  round((details->'summaryDTO'->>'groundContactTime')::numeric)      as ground_contact_ms,
  round((details->'summaryDTO'->>'verticalOscillation')::numeric, 1) as vertical_oscillation_cm,
  case when summary->'activityType'->>'typeKey' like '%swimming%' and (summary->>'distance')::numeric > 0
       then round((summary->>'movingDuration')::numeric / 60 / ((summary->>'distance')::numeric / 100), 2) end
                                                                     as swim_pace_min_per_100m,
  round((details->'summaryDTO'->>'averageSWOLF')::numeric)           as avg_swolf,
  round((details->'summaryDTO'->>'averageStrokes')::numeric, 1)      as avg_strokes_per_length,
  (details->'summaryDTO'->>'poolLength')::numeric                    as pool_length
from public.activities;

revoke all on public.activity_overview from anon, authenticated;
grant select on public.activity_overview to service_role;
