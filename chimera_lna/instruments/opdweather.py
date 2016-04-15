import logging
import re
import urllib2
import time
import datetime

from astropy import units
from chimera.core.exceptions import OptionConversionException
from chimera.core.lock import lock
from chimera.instruments.weatherstation import WeatherBase
from chimera.interfaces.weatherstation import WSValue
import numpy as np


class OpdWeather(WeatherBase):
    __config__ = {"model": "OPD 1.60m telescope weather station",
                  "check_interval": 3 * 60,  # in seconds
                  "uri": "http://200.131.64.185/clima/download.txt",
                  "temperature_treshold": 3,  # degrees to a temperature change be and event
                  "humidity_treshold": 3,  # % to a humidity change be and event
                  "wind_speed_treshold": 3,  # m/s to a wind change be and event
                  "dew_point_treshold": 3,  # degrees to a dew point change be and event
                  "pressure_treshold": 3,  # mm_hg to a pressure change be and event
                  "rain_treshold": 3,  # mm/h to a raing change be and event
                  }

    def __init__(self):
        WeatherBase.__init__(self)
        self.field_re = re.compile('[ ]*')  # Weather data file parse regexp
        self._last_check = 0
        self._date_ws = None
        self._time_ws = None

        # logging.
        # put every logger on behalf of chimera's logger so
        # we can easily setup levels on all our parts
        logName = self.__module__
        if not logName.startswith("chimera."):
            logName = "chimera." + logName + " (%s)" % logName

        self.log = logging.getLogger(logName)

        # For wind direction
        self._directions = np.array(
            ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"])
        self._angles = np.arange(0, 360, 360. / len(self._directions))
        self._directions = np.append(self._directions, ["---"])
        self._angles = np.append(self._angles, [0])

    def __start__(self):
        # self.setHz(self.__config__["check_interval"])
        pass

    def _get_http(self, uri):
        '''
        Connect and parse info from LNA weather station webpage.
        :param uri: Weather station address
        :return: Weather station raw measurements: date, time, temp_out, hum_out, dew_point, wind_speed, wind_dir,
                 pressure, rain, temp_in, hum_in
        '''

        self.log.info("Querying OPD meteo station...")
        try:
            url = urllib2.urlopen(uri)
        except urllib2.URLError, e:
            self.log.error('Error opening url %s: %s' % (uri, e))
            return False
        date_ws, time_ws, temp_out, hum_out, dew_point, wind_speed, wind_dir, pressure, rain = \
            np.array(self.field_re.split(url.readlines()[-1].strip()))[[0, 1, 2, 5, 6, 7, 8, 15, 16]]
        url.close()

        return date_ws, time_ws, temp_out, hum_out, dew_point, wind_speed, wind_dir, pressure, rain

    def _wind_direction(self, wind_dir_letters):
        '''
        :param wind_dir_letters: Up to three letter wind direction. Example: NNW
        :return: angle: Wind angle in degrees.
        '''
        return self._angles[int(np.argwhere(wind_dir_letters == self._directions))]

    @lock
    def _check(self):
        if time.time() >= self._last_check + self["check_interval"]:
            try:
                date_ws, time_ws, temp_out, hum_out, dew_point, wind_speed, wind_dir, pressure, rain = \
                    self._get_http(self["uri"])
            except TypeError:
                return False
            # TODO: check date and time.
            self._date_ws = date_ws
            self._time_ws = time_ws
            self._temperature = np.float(temp_out)
            self._humidity = np.float(hum_out)
            self._dew_point = np.float(dew_point)
            self._wind_speed = np.float(wind_speed)
            self._wind_dir = self._wind_direction(wind_dir)
            self._pressure = np.float(pressure)
            self._rain = np.float(rain)
            self._last_check = time.time()
            return True
        else:
            return True

    def obs_time(self):
        ''' Returns a string with local date/time of the meteorological observation
        '''
        if self._time_ws is None:
            return None
        d, m, y = self._date_ws.split('/')
        hour, min = self._time_ws.split(':')
        dt = datetime.datetime(int('20' + y), int(m), int(d), int(hour), int(min)) - (
            datetime.datetime.now() - datetime.datetime.utcnow())

        # return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")

    def humidity(self, unit_out=units.pct):

        if unit_out not in self.__accepted_humidity_units__:
            raise OptionConversionException("Invalid humidity unit %s." % unit_out)

        if self._check():
            return WSValue(self.obs_time(), self._convert_units(self._humidity, units.pct, unit_out), unit_out)
        else:
            return False

    def temperature(self, unit_out=units.Celsius):

        if unit_out not in self.__accepted_temperature_units__:
            raise OptionConversionException("Invalid temperature unit %s." % unit_out)

        if self._check():
            return WSValue(self.obs_time(), self._convert_units(self._temperature, units.Celsius, unit_out), unit_out)
        else:
            return False

    def wind_speed(self, unit_out=units.meter / units.second):

        if self._check():
            return WSValue(self.obs_time(), self._convert_units(self._wind_speed, (units.km / units.h), unit_out),
                           unit_out)
        else:
            return False

    def wind_direction(self, unit_out=units.degree):

        if self._check():
            return WSValue(self.obs_time(), self._convert_units(self._wind_dir, units.deg, unit_out), unit_out)
        else:
            return False

    def dew_point(self, unit_out=units.Celsius):

        if self._check():
            return WSValue(self.obs_time(), self._convert_units(self._dew_point, units.deg_C, unit_out), unit_out)
        else:
            return False

    def pressure(self, unit_out=units.Pa):
        if self._check():
            return WSValue(self.obs_time(), self._convert_units(self._pressure, units.bar / 1000, unit_out), unit_out)
        else:
            return False

    # def rain(self, deltaT=0, unit=Unit.MM_PER_H):
    #     # TODO: FIXME. Check rain units.
    #     if unit != Unit.MM_PER_H:
    #         return NotImplementedError()
    #     # self._check()
    #     # return self._temperature
    #     return NotImplementedError()

    def getMetadata(self, request):

        return [('ENVMOD', str(self['model']), 'Weather station Model'),
                ('ENVTEM', self.temperature(unit_out=units.deg_C).value, '[degC] Weather station temperature'),
                ('ENVHUM', self.humidity(unit_out=units.pct).value, '[%] Weather station relative humidity'),
                ('ENVWIN', self.wind_speed(unit_out=units.m / units.s).value, '[m/s] Weather station wind speed'),
                ('ENVDIR', self.wind_direction(unit_out=units.deg).value, '[deg] Weather station wind direction'),
                ('METDEW', self.dew_point(unit_out=units.deg_C).value, '[degC] Weather station dew point'),
                ('ENVPRE', self.pressure(unit_out=units.cds.mmHg).value, '[mmHg] Weather station air pressure'),
                # ('METRAIN', str(self.pressure()), 'Weather station rain indicator'),
                ('ENVDAT', self.obs_time(), 'Date of the meteo observation')  # FIXME: Must be UTC time.
                ]


if __name__ == '__main__':
    test = OpdWeather()
    print test.getMetadata(None)
