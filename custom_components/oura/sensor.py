"""Sensors from Oura Ring data."""

import datetime
from dateutil import parser
import enum
from homeassistant import const
from homeassistant.helpers import config_validation
from homeassistant.helpers import entity
import logging
import re
import voluptuous
from . import api
from . import views

_LOGGER = logging.getLogger(__name__)

# Constants.
_FULL_WEEKDAY_NAMES = [
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
    'sunday',
]

# Sensor config.
SENSOR = 'oura'
SENSOR_NAME = 'Oura Ring'

_CONF_CLIENT_ID = 'client_id'
_CONF_CLIENT_SECRET = 'client_secret'
_CONF_BACKFILL = 'max_backfill'
_CONF_NAME = 'name'

# Default attributes.
_DEFAULT_NAME = 'oura_ring'
_DEFAULT_MONITORED_VARIABLES = ['yesterday']
_DEFAULT_BACKFILL = 0

PLATFORM_SCHEMA = config_validation.PLATFORM_SCHEMA.extend({
    voluptuous.Required(_CONF_CLIENT_ID): config_validation.string,
    voluptuous.Required(_CONF_CLIENT_SECRET): config_validation.string,
    voluptuous.Optional(
        const.CONF_MONITORED_VARIABLES,
        default=_DEFAULT_MONITORED_VARIABLES): config_validation.ensure_list,
    voluptuous.Optional(_CONF_NAME, default=_DEFAULT_NAME): config_validation.string,
    voluptuous.Optional(
        _CONF_BACKFILL,
        default=_DEFAULT_BACKFILL): config_validation.positive_int,
})

_EMPTY_SENSOR_ATTRIBUTE = {
    'date': None,
    'bedtime_start_hour': None,
    'bedtime_end_hour': None,
    'breath_average': None,
    'temperature_delta': None,
    'resting_heart_rate': None,
    'heart_rate_average': None,
    'deep_sleep_duration': None,
    'rem_sleep_duration': None,
    'light_sleep_duration': None,
    'total_sleep_duration': None,
    'awake_duration': None,
    'in_bed_duration': None,
}

_EMPTY_READINESS_SENSOR_ATTRIBUTE = {
    'date': None,
    'score_activity_balance': None,
    'score_hrv_balance': None,
    'score_previous_day': None,
    'score_previous_night': None,
    'score_recovery_index': None,
    'score_resting_hr': None,
    'score_sleep_balance': None,
    'score_temperature': None,
}

class MonitoredDayType(enum.Enum):
  """Types of days which can be monitored."""
  UNKNOWN = 0
  YESTERDAY = 1
  WEEKDAY = 2
  DAYS_AGO = 3


async def setup(hass, config):
  """No set up required. Token retrieval logic handled by sensor."""
  return True


def setup_platform(hass, config, add_devices, discovery_info=None):
  """Adds sensor platform to the list of platforms."""
  client_id = config.get(_CONF_CLIENT_ID)
  client_secret = config.get(_CONF_CLIENT_SECRET)
  name = config.get(_CONF_NAME)
  oura_api = api.OuraApi(hass, client_id, client_secret, name)
  add_devices([OuraSleepSensor(config, oura_api, hass)], True)
  add_devices([OuraReadinessSensor(config, oura_api, hass)], True)


# Support functions for the sensors
def _seconds_to_hours(time_in_seconds):
  """Parses times in seconds and converts it to hours.
  Args:
    time_in_seconds: Time given in seconds

  Returns:
    Time in hours, rounded 2 decimals """
  return round(int(time_in_seconds) / (60 * 60), 2)


def _add_days_to_string_date(string_date, days_to_add):
  """Adds (or subtracts) days from a string date.

  Args:
    string_date: Original date in YYYY-MM-DD.
    days_to_add: Number of days to add. Negative to subtract.

  Returns:
    Date in YYYY-MM-DD with days added.
  """
  date = datetime.datetime.strptime(string_date, '%Y-%m-%d')
  new_date = date + datetime.timedelta(days=days_to_add)
  return str(new_date.date())

def _get_date_type_by_name(date_name):
  """Gets the type of date format based in the date name.

  Args:
    date_name: Date for which to verify type.

  Returns:
    Date type(MonitoredDayType).
  """
  if date_name == 'yesterday':
    return MonitoredDayType.YESTERDAY
  elif date_name in _FULL_WEEKDAY_NAMES:
    return MonitoredDayType.WEEKDAY
  elif 'd_ago' in date_name or 'days_ago' in date_name:
    return MonitoredDayType.DAYS_AGO
  else:
    return MonitoredDayType.UNKNOWN

def _get_date_by_name(date_name):
  """Translates a date name into YYYY-MM-DD format for the given day.

  Args:
    date_name: Name of the date to get. Supported:
      yesterday, weekday(e.g. monday, tuesday), Xdays_ago(e.g. 3days_ago).

  Returns:
    Date in YYYY-MM-DD format.
  """
  date_type = _get_date_type_by_name(date_name)
  today = datetime.date.today()
  days_ago = None
  if date_type == MonitoredDayType.YESTERDAY:
    days_ago = 1

  elif date_type == MonitoredDayType.WEEKDAY:
    date_index = _FULL_WEEKDAY_NAMES.index(date_name)
    days_ago = (
        today.weekday() - date_index
        if today.weekday() > date_index else
        7 + today.weekday() - date_index
    )

  elif date_type == MonitoredDayType.DAYS_AGO:
    digits_regex = re.compile(r'\d+')
    digits_match = digits_regex.match(date_name)
    if digits_match:
      try:
        days_ago = int(digits_match.group())
      except:
        days_ago = None

  if days_ago is None:
    _LOGGER.info("Oura: Unknown day name %s, using yesterday.", date_name) 
    days_ago = 1

  return str(today - datetime.timedelta(days=days_ago))

def _get_backfill_date(date_name, date_value):
  """Gets the backfill date for a given date and date name.

  Args:
    date_name: Date name to backfill.
    date_value: Last checked value.

  Returns:
    Potential backfill date. None if Unknown.
  """
  date_type = _get_date_type_by_name(date_name)

  if date_type == MonitoredDayType.YESTERDAY:
    return _add_days_to_string_date(date_value, -1)
  elif date_type == MonitoredDayType.WEEKDAY:
    return _add_days_to_string_date(date_value, -7)
  elif date_type == MonitoredDayType.DAYS_AGO:
    return _add_days_to_string_date(date_value, -1)
  else:
    return None

class OuraSleepSensor(entity.Entity):
  """Representation of an Oura Ring sleep sensor.

  Attributes:
    name: name of the sensor.
    state: state of the sensor.
    device_state_attributes: attributes of the sensor.

  Methods:
    update: updates sensor data.
  """

  def __init__(self, config, oura_api, hass):
    """Initializes the sensor."""

    self._config = config
    self._hass = hass
    self._api = oura_api
    # Sensor config.
    self._name = config.get(_CONF_NAME) + '_sleep'
    self._backfill = config.get(_CONF_BACKFILL)
    self._monitored_days = [
        date_name.lower()
        for date_name in config.get(const.CONF_MONITORED_VARIABLES)
    ]


    # Attributes.
    self._state = None  # Sleep score.
    self._attributes = {}


  def _parse_sleep_data(self, oura_data):
    """Processes sleep data into a dictionary.

    Args:
      oura_data: Sleep data in list format from Oura API.

    Returns:
      Dictionary where key is the requested summary_date and value is the
      Oura sleep data for that given day.
    """
    if not oura_data or 'sleep' not in oura_data:
      _LOGGER.error("Couldn\'t fetch data for Oura ring sensor.")
      return {}

    sleep_data = oura_data.get('sleep')
    if not sleep_data:
      return {}

    sleep_dict = {}
    for sleep_daily_data in sleep_data:
      sleep_date = sleep_daily_data.get('summary_date')
      if not sleep_date:
        continue
      sleep_dict[sleep_date] = sleep_daily_data

    return sleep_dict

  def update(self):
    """Fetches new state data for the sleep sensor."""
    sleep_dates = {
        date_name: _get_date_by_name(date_name)
        for date_name in self._monitored_days
    }

    # Add an extra week to retrieve past week in case current week data is
    # missing.
    start_date = _add_days_to_string_date(min(sleep_dates.values()), -7)
    end_date = max(sleep_dates.values())

    oura_data = self._api.get_oura_data('SLEEP', start_date, end_date)
    sleep_data = self._parse_sleep_data(oura_data)
  
    _LOGGER.info("SLEEP      : %s", oura_data)
    _LOGGER.info("SLEEP PARSE: %s", sleep_data)

    if not sleep_data:
      _LOGGER.info("No data, Returns")
      return

    for date_name, date_value in sleep_dates.items():
      if date_name not in self._attributes:
        self._attributes[date_name] = dict(_EMPTY_SENSOR_ATTRIBUTE)
        self._attributes[date_name]['date'] = date_value

      sleep = sleep_data.get(date_value)
      date_name_title = date_name.title()

      # Check past dates to see if backfill is possible when missing data.
      backfill = 0
      while (not sleep and
             backfill < self._backfill and
             date_value >= start_date):
        last_date_value = date_value
        date_value = _get_backfill_date(date_name, date_value)
        if not date_value:
          break

        _LOGGER.info("Unable to read Oura data for %s", date_name_title)
        _LOGGER.info("(%s). Fetching %s instead.", last_date_value, date_value)

        sleep = sleep_data.get(date_value)
        backfill += 1

      if not sleep:
        _LOGGER.error("Unable to read Oura data for %s.", date_name_title)
        continue

      # State gets the value of the sleep score for the first monitored day.
      if self._monitored_days.index(date_name) == 0:
        self._state = sleep.get('score')

      bedtime_start = parser.parse(sleep.get('bedtime_start'))
      bedtime_end = parser.parse(sleep.get('bedtime_end'))

      self._attributes[date_name] = {
          'date': date_value,

          # HH:MM at which you went bed.
          'bedtime_start_hour': bedtime_start.strftime('%H:%M'),
          # HH:MM at which you woke up.
          'bedtime_end_hour': bedtime_end.strftime('%H:%M'),

          # Breaths / minute.
          'breath_average': int(round(sleep.get('breath_average'), 0)),
          # Temperature deviation in Celsius.
          'temperature_delta': sleep.get('temperature_delta'),

          # Beats / minute (lowest).
          'resting_heart_rate': sleep.get('hr_lowest'),
          # Avg. beats / minute.
          'heart_rate_average': int(round((
              sum(sleep.get('hr_5min', 0)) /
              (len(sleep.get('hr_5min', [])) or 1)),
              0)),

          # Hours in deep sleep.
          'deep_sleep_duration': _seconds_to_hours(sleep.get('deep')),
          # Hours in REM sleep.
          'rem_sleep_duration': _seconds_to_hours(sleep.get('rem')),
          # Hours in light sleep.
          'light_sleep_duration': _seconds_to_hours(sleep.get('light')),
          # Hours sleeping: deep + rem + light.
          'total_sleep_duration': _seconds_to_hours(sleep.get('total')),
          # Hours awake.
          'awake_duration': _seconds_to_hours(sleep.get('awake')),
          # Hours in bed: sleep + awake.
          'in_bed_duration': _seconds_to_hours(sleep.get('duration')),
      }

  # Hass.io properties.
  @property
  def name(self):
    """Returns the name of the sensor."""
    return self._name

  @property
  def state(self):
    """Returns the state of the sensor."""
    return self._state

  @property
  def device_state_attributes(self):
    """Returns the sensor attributes."""
    return self._attributes

class OuraReadinessSensor(entity.Entity):
  """Representation of an Oura Ring readiness sensor.

  Attributes:
    name: name of the sensor.
    state: state of the sensor.
    device_state_attributes: attributes of the sensor.

  Methods:    
    update: updates sensor data.
  """

  def __init__(self, config, oura_api, hass):
    """Initializes the sensor."""

    self._config = config
    self._hass = hass
    self._api = oura_api
    # Sensor config.
    self._name = config.get(_CONF_NAME) + '_readiness'
    self._backfill = config.get(_CONF_BACKFILL)
    self._monitored_days = [
        date_name.lower()
        for date_name in config.get(const.CONF_MONITORED_VARIABLES)
    ]

    # Attributes.
    self._state = None  # Readiness score.
    self._attributes = {}

  def _parse_readiness_data(self, oura_data):
    """Processes readiness data into a dictionary.

    Args:
      oura_data: Sleep data in list format from Oura API.

    Returns:
      Dictionary where key is the requested summary_date and value is the
      Oura readiness data for that given day.
    """
    if not oura_data or 'readiness' not in oura_data:
      _LOGGER.error("Couldn\'t fetch data for Oura ring sensor.")
      return {}

    readiness_data = oura_data.get('readiness')
    if not readiness_data:
      return {}

    readiness_dict = {}
    for readiness_daily_data in readiness_data:
      readiness_date = readiness_daily_data.get('summary_date')
      if not readiness_date:
        continue
      readiness_dict[readiness_date] = readiness_daily_data

    return readiness_dict

  def update(self):
    """Fetches new state data for the sensor."""
    readiness_dates = {
        date_name: _get_date_by_name(date_name)
        for date_name in self._monitored_days
    }

    # Add an extra week to retrieve past week in case current week data is
    # missing.
    start_date = _add_days_to_string_date(min(readiness_dates.values()), -7)
    end_date = max(readiness_dates.values())

    oura_data = self._api.get_oura_data('READINESS', start_date, end_date)
    readiness_data = self._parse_readiness_data(oura_data)
    
    _LOGGER.info("READINESS  : %s", oura_data)
    _LOGGER.info("READI PARSE: %s", readiness_data)

    if not readiness_data:
      _LOGGER.info("No data, Returns")
      return

    for date_name, date_value in readiness_dates.items():
      if date_name not in self._attributes:
        self._attributes[date_name] = dict(_EMPTY_READINESS_SENSOR_ATTRIBUTE)
        self._attributes[date_name]['date'] = date_value

      readiness = readiness_data.get(date_value)
      date_name_title = date_name.title()

      # Check past dates to see if backfill is possible when missing data.
      backfill = 0
      while (not readiness and
             backfill < self._backfill and
             date_value >= start_date):
        last_date_value = date_value
        date_value = _get_backfill_date(date_name, date_value)
        if not date_value:
          break

        _LOGGER.info("Unable to read Oura data for %s", date_name_title)
        _LOGGER.info("(%s). Fetching %s instead.", last_date_value, date_value)

        readiness = readiness_data.get(date_value)
        backfill += 1

      if not readiness:
        _LOGGER.error("Unable to read Oura data for %s.", date_name_title) 
        continue

      # State gets the value of the readiness score for the first monitored day.
      if self._monitored_days.index(date_name) == 0:
        self._state = readiness.get('score')


      self._attributes[date_name] = {
          'date': date_value,

          # Activity level last days impact on readiness
          'score_activity_balance': readiness.get('score_activity_balance'),
          # Heart Rate Variability trend
          'score_hrv_balance': readiness.get('score_hrv_balance'),

          # Last days physical activity
          'score_previous_day': readiness.get('score_previous_day'),
          # Sleep score last night
          'score_previous_night': readiness.get('score_previous_night'),
          # How long it takes for resting heart rate stabilize
          'score_recovery_index': readiness.get('score_recovery_index'),
          # Beats / Minute
          'score_resting_hr': readiness.get('score_resting_hr'),
          # Sleep balance vs need
          'score_sleep_balance': readiness.get('score_sleep_balance'),
          # Variation in body temperature
          'score_temperature': readiness.get('score_temperature'),
      }

  # Hass.io properties.
  @property
  def name(self):
    """Returns the name of the sensor."""
    return self._name

  @property
  def state(self):
    """Returns the state of the sensor."""
    return self._state

  @property
  def device_state_attributes(self):
    """Returns the sensor attributes."""
    return self._attributes