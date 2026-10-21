from datetime import datetime

from pydantic import BaseModel

from app.constants.sleep import SleepStageType


class SleepStage(BaseModel):
    """A single continuous sleep stage interval stored inside the JSONB column."""

    stage: SleepStageType
    start_time: datetime
    end_time: datetime
    # The label the instrument itself used, verbatim, where that is not one of OW's
    # words: a PSG scorer says N1/N2/N3, and folding that to light/deep is a choice the
    # consumer makes at comparison time, not one the store makes on the way in. Set by
    # the manual reference import; None on every provider path, whose `stage` is
    # already the instrument's word. When set and `stage` is `unknown`, the interval
    # is scored - just not in OW's alphabet.
    vendor_stage: str | None = None
