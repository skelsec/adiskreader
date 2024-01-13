# -*- coding: utf-8 -*-

"""Enhancement of datetime for DOS date/time format compatibility."""
from datetime import datetime, time, timezone


def _convert_to_dos_date(func):
    """Convert datetime to DosDateTime."""
    def _wrapper(*args, **kwargs) -> "DosDateTime":
        date: datetime = func(*args, **kwargs)
        return DosDateTime(*date.timetuple()[:6])
    return _wrapper


class DosDateTime(datetime):
    """DOS-specific date/time format serialization."""

    now = _convert_to_dos_date(datetime.now)
    fromtimestamp = _convert_to_dos_date(datetime.fromtimestamp)

    def serialize_date(self) -> int:
        """Convert current datetime to FAT date."""
        date = self.year - 1980 << 9 | self.month << 5 | self.day
        return date

    def serialize_time(self) -> int:
        """Convert current datetime to FAT time."""
        time = self.hour << 11 | self.minute << 5
        time |= ((self.second - (self.second % 2)) // 2)
        return time

    @staticmethod
    def deserialize_date(dt: int) -> "DosDateTime":
        """Convert a DOS date format to a Python object."""
        dt = int(dt)
        day = dt & (1 << 5) - 1
        month = (dt >> 5) & ((1 << 4) - 1)
        year = ((dt >> 9) & (1 << 7) - 1) + 1980

        try:
            return DosDateTime(year, month, day)
        except ValueError:
            return DosDateTime(1980, 1, 1)

    @staticmethod
    def deserialize_time(tm: int) -> time:
        """Convert a DOS time format to a Python object."""
        tm = int(tm)
        second = (tm & (1 << 5) - 1) * 2
        minute = (tm >> 5) & ((1 << 6) - 1)
        hour = (tm >> 11) & ((1 << 5) - 1)

        try:
            return time(hour, minute, second)
        except ValueError:
            return time(0, 0, 0)
        
    @staticmethod
    def convert_dt(dt:int, tm:int) -> datetime:
        """Convert DOS date/time format to a Python object."""
        return datetime.combine(DosDateTime.deserialize_date(dt),
                                DosDateTime.deserialize_time(tm),
                                tzinfo=timezone.utc)   
