"""Defines Oura platform views."""

from homeassistant import core
from homeassistant.components import http
import json
import logging

_LOGGER = logging.getLogger(__name__)

# Views configuration.
AUTH_CALLBACK_NAME = 'api:oura'
AUTH_CALLBACK_PATH = '/oura/oauth/setup'


class OuraAuthCallbackView(http.HomeAssistantView):
  """Oura Authorization Callback View.

  Methods:
    get: Handles get requests to given view.
  """

  requires_auth = False
  url = AUTH_CALLBACK_PATH
  name = AUTH_CALLBACK_NAME

  def __init__(self, api):
    """Initializes view.

    Args:
      sensor: Sensor which initialized the OAuth process.
    """
    self._api = api

  @core.callback
  def get(self, request):
    """Handles Oura OAuth callbacks.

    Stores code from Oura API into cache token file.
    This code will be read by the API and use it to retrieve access token.
    """
    code = request.query.get('code')
    code_data = {'code': code}

    sensor_name = request.query.get('state')
    token_file_name = self._api.token_file_name
    _LOGGER.info("token_file_name %s.", token_file_name) 

    with open(token_file_name, 'w+') as token_file:
      _LOGGER.info("writing to token file") 
      token_file.write(json.dumps(code_data))

    self._api._get_access_token_data_from_file()

    return self.json_message(
        f'Oura OAuth code {code} for sensor.{sensor_name} stored in '
        f'{token_file_name}. The sensor API will use this code to retrieve '
        'the access token, store it and start fetching data on the next '
        'update. No further action is required from your side. Any errors on '
        'retrieving the API token will be logged. If you ever want to restart '
        f'this OAuth process, simply delete the file {token_file_name} within '
        'the /config/ directory.')
