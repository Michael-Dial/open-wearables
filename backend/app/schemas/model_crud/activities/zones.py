from pydantic import BaseModel


class HRZone(BaseModel):
    zone: int
    seconds: float
    max_bpm: int | None = None


class HRZones(BaseModel):
    zones: list[HRZone]
    max_hr: int | None = None
    threshold_hr: int | None = None
    resting_hr: int | None = None
    # What the zone boundaries are a percentage of, as the file states it (FIT hr_zone_calc:
    # "percent_max_hr", "percent_hrr", "percent_lthr", "custom"). None when the file does not say.
    calc_type: str | None = None


class PowerZone(BaseModel):
    zone: int
    seconds: float
    max_watts: int | None = None


class PowerZones(BaseModel):
    zones: list[PowerZone]
    ftp_watts: int | None = None
    # FIT pwr_zone_calc: "percent_ftp" or "custom". None when the file does not say.
    calc_type: str | None = None
