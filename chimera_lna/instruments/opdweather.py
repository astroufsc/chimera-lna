import re
import urllib2
import numpy as np

field_re = re.compile('[ ]*')

url = urllib2.urlopen('http://200.131.64.185/clima/download.txt')
date, time, temp_out, hum_out, dew_point, wind_speed, wind_dir, pressure, rain, temp_in, hum_in = np.array(field_re.split(url.readlines()[-1]))[[0,1,2,5,6,7,8,15,16,20,21]] 

print date, time, temp_out, hum_out, dew_point, wind_speed, wind_dir, pressure, rain, temp_in, hum_in

url.close()
