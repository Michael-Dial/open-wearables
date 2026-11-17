from app.schemas.enums import SeriesType
from app.schemas.model_crud.activities import StatColumn

# workout_details column -> the series and statistic it holds, mirrored into event_record_aggregate.
# Energy is not here: its series depends on the provider, which names it in EventRecordDetailCreate.energy_series.
WORKOUT_DETAIL_STATS: dict[str, tuple[SeriesType, StatColumn]] = {
    "heart_rate_min": (SeriesType.heart_rate, "min"),
    "heart_rate_avg": (SeriesType.heart_rate, "avg"),
    "heart_rate_max": (SeriesType.heart_rate, "max"),
    "steps_count": (SeriesType.steps, "sum"),
    "distance": (SeriesType.distance, "sum"),
    "average_speed": (SeriesType.speed, "avg"),
    "max_speed": (SeriesType.speed, "max"),
    "average_watts": (SeriesType.power, "avg"),
    "max_watts": (SeriesType.power, "max"),
    "average_cadence": (SeriesType.cadence, "avg"),
    "elev_low": (SeriesType.elevation, "min"),
    "total_elevation_gain": (SeriesType.elevation_gain, "sum"),
    "elev_high": (SeriesType.elevation, "max"),
}

# Per-workout figures a provider computes that have no workout_details column: returned as the workout's
# provider_metrics, keyed by series type, from the provider's session value in its aggregation column.
WORKOUT_PROVIDER_METRICS: frozenset[SeriesType] = frozenset(
    {
        SeriesType.aerobic_training_effect,
        SeriesType.anaerobic_training_effect,
        SeriesType.training_load_peak,
        SeriesType.threshold_heart_rate,
        SeriesType.functional_threshold_power,
        SeriesType.polar_running_index,
        SeriesType.polar_training_load,
        SeriesType.polar_cardio_load,
        SeriesType.polar_muscle_load,
        SeriesType.polar_perceived_load,
    }
)

# Workout statistics arriving as samples that get no session row: legacy SDK totalEnergyBurned (undecided) and
# totalDistance, whose sample series is distance_walking_running while the workout's distance series is distance.
UNDECIDED_WORKOUT_SERIES: frozenset[SeriesType] = frozenset(
    {SeriesType.active_energy, SeriesType.distance_walking_running}
)
