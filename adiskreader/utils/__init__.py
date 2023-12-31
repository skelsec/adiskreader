import datetime

def filetime_to_dt(filetime):
    # Define the FILETIME base: January 1, 1601
    base = datetime.datetime(1601, 1, 1)

    # Convert FILETIME to microseconds
    microseconds = filetime / 10

    # Return the calculated datetime
    return base + datetime.timedelta(microseconds=microseconds)