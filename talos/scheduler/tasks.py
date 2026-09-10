import os
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from talos.messages import Message, VoicePayload
from talos.services import display_power, sleep_mode

TZ       = ZoneInfo("America/New_York")  # pick your local tz
BROKER   = "192.168.1.160"
PORT     = 1883
city     = "Ellicott City,MD,US"
open_weather_api_key = os.getenv("OPEN_WEATHER_API_KEY")


def _weather_manager():
    try:
        from pyowm import OWM
    except ImportError as exc:
        raise RuntimeError(
            "pyowm is required for scheduled weather updates. "
            "Install project dependencies with: python3 -m pip install -r requirements.txt"
        ) from exc
    return OWM(open_weather_api_key).weather_manager()

def degrees_to_compass(deg):
    directions = [
        "N","NNE","NE","ENE","E","ESE","SE","SSE",
        "S","SSW","SW","WSW","W","WNW","NW","NNW"
    ]
    idx = int((deg + 11.25) / 22.5) % 16
    return directions[idx]

# ==== SPECIFIC TIME BASED FUNCTIONS ==================================================================================

#Define Time based jobs
def debug_job(gui_queue, central_queue=None):
    print("DEBUG JOB ACTIVATED")
    
def wake_display():
    """Morning wake-up: leave sleep mode, which lights the display with it.

    Waking and re-illuminating the screen are the same act, so this no longer
    publishes to ``tv_display/wake_status`` itself -- ``sleep_mode.wake()``
    drives that through ``talos.services.display_power``. This job stays as the
    backstop for the morning briefing, which clears sleep mode on its own when
    it is delivered (see talos/text/server.py) but may be disabled, deferred,
    or have nothing worth saying.

    ``wake()`` is called unconditionally rather than only when asleep: the
    display command is re-asserted with it, so a screen left dark by a missed
    command still comes back in the morning.
    """
    print("Waking Display.")
    try:
        sleep_mode.wake(reason="morning wake_display job")
    except RuntimeError as exc:
        print(f"Could not clear sleep mode: {exc}")
        # The flag could not be written, but the screen must still come up.
        display_power.apply(asleep=False)

def dim_display():
    """Nightly quiet-down: enter sleep mode, which darkens the display with it.

    This used to call ``tv_control.night_sleep()`` directly and leave the sleep
    flag alone, so 11pm produced a dark TV over a system that still believed it
    was awake -- the mirror image of asking for sleep mode and getting a lit
    screen. Both now go through the one flag.
    """
    print("Dimming Display.")
    try:
        sleep_mode.sleep(reason="nightly dim_display job")
    except RuntimeError as exc:
        print(f"Could not enter sleep mode: {exc}")
        display_power.apply(asleep=True)

def update_infopanel_information(gui_queue, central_queue=None):
    # fetch data here
    # This will NOT BLOCK infopanel GUI or voice commands because its in a separate thread
    mgr         = _weather_manager()
    observation = mgr.weather_at_place(city)
    weather     = observation.weather

    temp_data = weather.temperature("fahrenheit")
    wind_data = weather.wind(unit="miles_hour")

    push_status(
        central_queue,
        # CoinGecko fetch disabled to avoid recurring SSL errors in the scheduler.
        btc_price = "N/A",
        eth_price = "N/A",
        sol_price = "N/A",

        temp      = round(temp_data["temp"]),
        feelslike = round(temp_data["feels_like"]),
        humidity  = round(weather.humidity),
        wind      = round(wind_data.get("speed")),
        wind_dir  = degrees_to_compass(wind_data.get("deg")),
        weather   = weather.detailed_status,

        uptime = "ERR"
    )

def update_dynamo_information(gui_queue, central_queue=None):
    None


# VOICE FUNCTIONS #####################################################################################################################################
def morning_report_job(gui_queue, central_queue=None):
    mgr         = _weather_manager()
    observation = mgr.weather_at_place(city)
    weather     = observation.weather
    temp_data   = weather.temperature("fahrenheit")

    temp        = round(temp_data["temp"])
    feelslike   = round(temp_data["feels_like"])
    weather     = weather.detailed_status

    now         = datetime.now()
    time_str    = now.strftime("%I:%M %p").lstrip("0")
    day_str     = now.strftime("%A")
    date_str    = now.strftime("%m-%d-%y")

    if central_queue is None:
        return
    central_queue.put(
        Message(
            type="voice_cmd",
            payload=VoicePayload(
            f"[Generate a morning report for the following data. This is not a spoken input, this is a system prompt]" \
            
            f"time: {time_str}" \
            f"day: {day_str}" \
            f"Temperature: {temp} deg F" \
            f"feels like: {feelslike} deg F" \
            f"weather: {weather}" \
            f"calendar: None" \
            f"Date: {date_str}" \
            )
        )
    )



# Start the chron job scheduler ########################################################################################################################
def push_status(central_queue, **values):
    if central_queue is None:
        return
    central_queue.put(Message(type="ui", payload=("STATUS", values)))


def start_scheduler(gui_queue, central_queue=None):
    scheduler = BackgroundScheduler(
        timezone     = TZ,
        job_defaults = {
            "coalesce"           : True,        # merge backlogged runs into one --- might not need this
            "max_instances"      : 1,           # don’t overlap the same job
            "misfire_grace_time" : 600          # seconds; OK to fire within 10 mins if late
        },
    )

    #Debug Job
    # Disabled: fired at 1:31 PM daily (the "7:30 AM" comment was wrong) and
    # only printed to stdout.
    #scheduler.add_job(
    #    debug_job,
    #    trigger  = CronTrigger(hour=13, minute=31),
    #    args     = [gui_queue, central_queue],
    #    id       = "debug_task",
    #    replace_existing = True,
    #)

    scheduler.add_job(
        wake_display,
        trigger  = CronTrigger(hour=7, minute=25), # Daily at 7:25 AM, to ensure this happens well before morning MOTD
        args     = None,
        id       = "wake_display",
        replace_existing = True,
    )

    scheduler.add_job(
        dim_display,
        trigger  = CronTrigger(hour=23, minute=0), # Daily at 11:00 PM
        args     = None,
        id       = "dim_display",
        replace_existing = True,
    )

    scheduler.add_job(
        update_infopanel_information,
        trigger  = CronTrigger(minute="*/1"), # Every 1 Minute
        args     = [gui_queue, central_queue],
        id       = "update_infopane_information",
        replace_existing = True,
    )

    # Disabled: the morning wake-up report moves to the awareness subsystem.
    # This job pushed a "Generate a morning report..." voice_cmd, which the
    # request classifier matched on "generate" and routed to a background job,
    # so 7:30 AM only ever spoke the background acknowledgement
    # ("I can do that. I'm working on it now.") and never the report itself.
    #scheduler.add_job(
    #    morning_report_job,
    #    trigger  = CronTrigger(hour=7, minute=30),
    #    args     = [gui_queue, central_queue],
    #    id       = "morning_report_job",
    #    replace_existing = True,
    #)

    #scheduler.add_job(
    #    get_crypto_prices,
    #    trigger  = CronTrigger(minute="*/15"), # Every 15 Minutes
    #    args     = None,
    #    id       = "get_crypto_prices",
    #    replace_existing = True,
    #)

    #More jobs can be added here.

    scheduler.start()
    return scheduler

#wake_display()
#dim_display()
