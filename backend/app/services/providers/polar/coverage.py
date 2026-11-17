from app.schemas.enums import SeriesType
from app.schemas.enums.health_score_category import HealthScoreCategory

# Daily-activity mapping (DailyActivityJSON attribute → SeriesType) consumed
# directly by data_247.normalize_daily_activity via /v3/users/activities.
ACTIVITY_SERIES: dict[str, SeriesType] = {
    "steps": SeriesType.steps,
    "active_calories": SeriesType.active_energy,
    "basal_calories": SeriesType.basal_energy,
    "distance_from_steps": SeriesType.distance_walking_running,
    "active_time_minutes": SeriesType.active_time,
    "exercise_time_minutes": SeriesType.exercise_time,
}

# Profile settings (PhysicalInfoJSON attribute → SeriesType) from /v3/users/physical-info. Their own
# series: Polar does not say whether a figure was measured, typed in or defaulted.
PHYSICAL_INFO_SERIES: dict[str, SeriesType] = {
    "aerobic_threshold": SeriesType.polar_aerobic_threshold,
    "anaerobic_threshold": SeriesType.polar_anaerobic_threshold,
    "vo2_max": SeriesType.polar_profile_vo2_max,
    "maximum_heart_rate": SeriesType.polar_profile_max_heart_rate,
    "resting_heart_rate": SeriesType.polar_profile_resting_heart_rate,
}

# Per-exercise figures (/v3/exercises), each filed as a session value of its workout.
# ExerciseJSON attribute → SeriesType, then TrainingLoadProJSON attribute → SeriesType.
EXERCISE_SESSION_SERIES: dict[str, SeriesType] = {
    "running_index": SeriesType.polar_running_index,
    "training_load": SeriesType.polar_training_load,
}
TRAINING_LOAD_PRO_SERIES: dict[str, SeriesType] = {
    "cardio_load": SeriesType.polar_cardio_load,
    "muscle_load": SeriesType.polar_muscle_load,
    "perceived_load": SeriesType.polar_perceived_load,
}

TIMESERIES: frozenset[SeriesType] = frozenset(
    {
        *ACTIVITY_SERIES.values(),  # /v3/users/activities
        *PHYSICAL_INFO_SERIES.values(),  # /v3/users/physical-info
        *EXERCISE_SESSION_SERIES.values(),  # /v3/exercises (session values)
        *TRAINING_LOAD_PRO_SERIES.values(),  # /v3/exercises training_load_pro (session values)
        SeriesType.heart_rate,  # /v3/users/sleep + /v3/users/continuous-heart-rate + /v3/users/wrist-ecg
        # /v3/users/nightly-recharge 5-min windows + /v3/users/spo2 + /v3/users/wrist-ecg
        SeriesType.heart_rate_variability_rmssd,
        SeriesType.respiratory_rate,  # /v3/users/nightly-recharge
        SeriesType.rr_interval,  # v4 training-sessions rrSamples, else /v3/exercises sample type 11
        SeriesType.pulse_to_pulse_interval,  # v4 /ppi-samples (optical, not ECG)
        SeriesType.oxygen_saturation,  # /v3/users/spo2
        SeriesType.skin_temperature,  # /v3/users/sleep-skin-temperature + /v3/users/body-temperature (SKIN)
        SeriesType.skin_temperature_deviation,  # /v3/users/sleep-skin-temperature (deviation_from_baseline)
        SeriesType.body_temperature,  # /v3/users/body-temperature (CORE)
    }
)

# EventRecordDetail fields populated by workouts.py (workout records)
WORKOUT_FIELDS: frozenset[str] = frozenset(
    {
        "heart_rate_max",
        "heart_rate_avg",
        "energy_burned",
        "distance",
    }
)

# EventRecordDetail fields populated by data_247.py (sleep records)
SLEEP_FIELDS: frozenset[str] = frozenset(
    {
        "sleep_total_duration_minutes",
        "sleep_time_in_bed_minutes",
        "sleep_efficiency_score",
        "sleep_deep_minutes",
        "sleep_rem_minutes",
        "sleep_light_minutes",
        "sleep_awake_minutes",
        "sleep_stages",
    }
)

HEALTH_SCORES: frozenset[HealthScoreCategory] = frozenset(
    {
        HealthScoreCategory.SLEEP,
        HealthScoreCategory.STRAIN,
        HealthScoreCategory.RECOVERY,
        HealthScoreCategory.READINESS,
    }
)
