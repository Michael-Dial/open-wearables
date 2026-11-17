from pydantic import BaseModel


class PhysicalInfoJSON(BaseModel):
    """GET /v3/users/physical-info: the user's current Polar profile, one object.

    Written against the AccessLink spec (https://www.polar.com/accesslink-api/, "Physical info").
    Every field is optional there. ``modified`` is when the profile last changed, any field of
    it; Polar keeps no per-field date and no source for the heart rate figures or VO2max
    (only ``weight_source``), so a value cannot be told apart from one the user typed in.
    """

    created: str | None = None
    modified: str | None = None
    maximum_heart_rate: int | None = None
    resting_heart_rate: int | None = None
    aerobic_threshold: int | None = None
    anaerobic_threshold: int | None = None
    vo2_max: int | None = None
    training_background: str | None = None
