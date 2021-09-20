# Custom Air Quality Sensor and Measurement Dashboard with Adafruit, Prometheus, and Grafana

Create your own air quality sensor and view its measurements with Grafana!

## Hardware requirements

* [Adafruit FT232H Breakout - General Purpose USB to GPIO, SPI, I2C](https://learn.adafruit.com/circuitpython-on-any-computer-with-ft232h)
* [Adafruit PMSA003I Air Quality Breakout](https://learn.adafruit.com/pmsa003i)
* [Adafruit SCD-30 - NDIR CO2 Temperature and Humidity Sensor](https://learn.adafruit.com/adafruit-scd30)
* [STEMMA QT cables](https://www.adafruit.com/product/4210) (or other wires / breadboard to connect components)
* USB-C power cable

## Software requirements
* [Python](https://www.python.org/downloads/)
* [Prometheus](https://prometheus.io/docs/prometheus/latest/getting_started/)
* [Grafana](https://grafana.com/docs/grafana/latest/getting-started/getting-started/)

## System assumptions
* The air_qual_measure.py script is written for a windows system. It will need adaptation to work on other OSs, but nothing precludes this sensor system from working on other OSs.
* The air_qual_measure.py assumes that the location of the prometheus.exe is one directory above relative to it. Change this location if needed.

## Instructions

* Follow the linked instructions above to install the required software.
* Connect the PMSA003I and SCD-30 to the FT232H, either with STEMMA QT cables or via wiring / breadboarding.
* Connect the FT232H to your computer via USB-C.
* Run the [post install checks](https://learn.adafruit.com/circuitpython-on-any-computer-with-ft232h/troubleshooting) to make sure the FT232H is detectable and working.
* Run the air_qual_measure script.
* Launch Grafana at http://localhost:3000/
* Follow Grafana instructions to set up your dashboard and connect to the prometheus data source. 
** If you wish, import the Environmental Statistics-1632112723401.json file in Grafana to use the dashboard I set up.
