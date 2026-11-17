from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class HeartRateJSON(BaseModel):
    average: int | None = None
    maximum: int | None = None


class HRSamplesJSON(BaseModel):
    """One sample array of an exercise.

    The published spec writes these keys kebab-case with a string type
    (``"sample-type": "11"``), but /v3/exercises sends snake_case with an integer
    (``"sample_type": 11``), as seen live on 2026-11-12. Reading only the spec's keys left
    every sample untyped, so no chest-strap session stored a single RR beat and nothing
    failed. Both spellings are read and the type is kept as text.
    """

    # AccessLink sends this null on RR (sample type 11), which has no fixed rate. As a
    # required int it rejected every chest-strap exercise, and every RR beat with it.
    recording_rate: int | None = Field(None, validation_alias=AliasChoices("recording_rate", "recording-rate"))
    sample_type: str | None = Field(None, validation_alias=AliasChoices("sample_type", "sample-type"))
    data: str | None = None

    @field_validator("sample_type", mode="before")
    @classmethod
    def _type_as_text(cls, value: object) -> object:
        return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


class HRZoneJSON(BaseModel):
    index: int
    lower_limit: int = Field(alias="lower-limit")
    upper_limit: int = Field(alias="upper-limit")
    in_zone: str = Field(alias="in-zone")


class RoutePointJSON(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    time: str | None = None
    satellites: int | None = None
    fix: int | None = None


def _both(name: str) -> AliasChoices:
    """A key in either spelling: the spec writes kebab-case, /v3/exercises has sent snake_case (see HRSamplesJSON)."""
    return AliasChoices(name, name.replace("_", "-"))


class TrainingLoadProJSON(BaseModel):
    """An exercise's Training Load Pro figures. Keys read in both spellings, as HRSamplesJSON's are.

    Unread keys are kept (``extra="allow"``) so an object whose loads all parse to nothing can
    be told apart from one Polar sent empty: the first is a mapping that no longer matches.
    """

    model_config = ConfigDict(extra="allow")

    date: str | None = None
    cardio_load: float | None = Field(None, validation_alias=_both("cardio_load"))
    muscle_load: float | None = Field(None, validation_alias=_both("muscle_load"))
    perceived_load: float | None = Field(None, validation_alias=_both("perceived_load"))
    cardio_load_interpretation: str | None = Field(None, validation_alias=_both("cardio_load_interpretation"))
    muscle_load_interpretation: str | None = Field(None, validation_alias=_both("muscle_load_interpretation"))
    perceived_load_interpretation: str | None = Field(None, validation_alias=_both("perceived_load_interpretation"))
    user_rpe: str | None = Field(None, validation_alias=_both("user_rpe"))


class ExerciseJSON(BaseModel):
    id: str
    upload_time: str | None = None
    polar_user: str | None = None
    device: str
    device_id: str | None = None

    sport: str
    detailed_sport_info: str | None = None

    start_time: str
    start_time_utc_offset: int
    duration: str

    calories: int | None = None
    # Metres. AccessLink types this as a number and sends fractions (1600.2), which an
    # int field rejects.
    distance: float | None = None
    heart_rate: HeartRateJSON | None = None
    heart_rate_zones: list[HRZoneJSON] | None = None

    training_load: float | None = None
    training_load_pro: TrainingLoadProJSON | None = Field(None, validation_alias=_both("training_load_pro"))
    has_route: bool | None = None

    fat_percentage: int | None = None
    carbohydrate_percentage: int | None = None
    protein_percentage: int | None = None
    running_index: int | None = Field(None, validation_alias=_both("running_index"))

    club_id: int | None = None
    club_name: str | None = None

    samples: list[HRSamplesJSON] | None = None
    route: list[RoutePointJSON] | None = None
