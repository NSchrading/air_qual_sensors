"""Measure air quality metrics via various Adafruit sensors and expose them via prometheus.
"""
import logging
import subprocess
import threading
import time

import adafruit_scd30
import board
import busio
import requests
from adafruit_pm25.i2c import PM25_I2C
from prometheus_client import Gauge, start_http_server
from pyftdi.i2c import I2cNackError

SERVER_PORT = 8090


def setup_logger() -> logging.Logger:
    """Configure loggers for this script"""
    logger = logging.getLogger("air_qual_measure")
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        filename="air_qual_measure.log",
        filemode="w",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    file_handler = logging.FileHandler("air_qual_measure.log")
    file_handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def initialize_sensors():
    """Initialize Adafruit sensors and return them."""
    # SCD-30 has tempermental I2C with clock stretching, and delays
    # It's best to start using I2C clock slower and then you can increase it
    # until the sensor stops responding (NAK fails, etc)
    # 4450 was the ~highest frequency I could use before getting I2cNackErrors
    logger = logging.getLogger("air_qual_measure")
    logger.debug("Initializing sensors...")
    i2c = busio.I2C(board.SCL, board.SDA, frequency=4450)  # for FT232H, use 1KHz
    scd = adafruit_scd30.SCD30(i2c)

    scd.temperature_offset = 3
    logger.debug("Temperature offset: %s", scd.temperature_offset)
    logger.debug("Measurement interval: %s", scd.measurement_interval)
    logger.debug("Self-calibration enabled: %s", scd.self_calibration_enabled)

    scd.ambient_pressure = 1012
    logger.debug("Ambient pressure: %s mbar", scd.ambient_pressure)

    scd.altitude = 32
    logger.debug("Altitude: %s meters above sea level", scd.altitude)

    reset_pin = None
    pm25 = PM25_I2C(i2c, reset_pin)

    return scd, pm25


def read_from_pm25(pm25):
    """Read air quality particulate matter values from the PM25 sensor and return them."""
    # See https://publiclab.org/questions/samr/04-07-2019/how-to-interpret-pms5003-sensor-values#c23772
    # for explanation of what these values mean
    # https://en.wikipedia.org/wiki/Air_quality_index
    logger = logging.getLogger("air_qual_measure")
    busio.I2C(board.SCL, board.SDA, frequency=100000)
    try:
        aqdata = pm25.read()
        return (
            aqdata["pm10 standard"],
            aqdata["pm25 standard"],
            aqdata["pm100 standard"],
            aqdata["particles 03um"],
            aqdata["particles 05um"],
            aqdata["particles 10um"],
            aqdata["particles 25um"],
            aqdata["particles 50um"],
            aqdata["particles 100um"],
        )
    except (RuntimeError, I2cNackError):
        logger.exception("Exception encountered reading data from PM25")

    return None


def read_from_scd(scd):
    """Read carbon dioxide, temperature, and relative humidity measurements from the SCD sensor
    and return them.
    """
    logger = logging.getLogger("air_qual_measure")
    busio.I2C(board.SCL, board.SDA, frequency=4450)
    try:
        # since the measurement interval is long (2+ seconds) we check for new data before reading
        # the values, to ensure current readings.
        if scd.data_available:
            return scd.CO2, scd.temperature, scd.relative_humidity
    except (RuntimeError, I2cNackError):
        logger.exception("Exception encountered reading data from SCD")

    return None


def log_from_subprocess(proc):
    """Continuously log any output lines from the given subprocess.
    Note: This function blocks, so it should be run in a separate thread
    if asynch behavior is desired."""
    logger = logging.getLogger("prometheus")
    for line in proc.stdout:
        logger.debug(line)


def start_main_prometheus_server():
    """Start the main prometheus server that will consume metrics from exporters
    and expose that data to grafana"""
    logger = logging.getLogger("air_qual_measure")
    logger.debug("Starting main prometheus server...")
    prometheus_server_bin_loc = "../prometheus-2.30.0.windows-amd64/prometheus.exe"
    config_file_path = "prometheus.yml"
    retention_time = "60d"

    proc = subprocess.Popen(  # pylint: disable=consider-using-with
        [
            prometheus_server_bin_loc,
            f"--config.file={config_file_path}",
            f"--storage.tsdb.retention.time={retention_time}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Make sure it started and hasn't exited
    assert proc.poll() is None

    logger.debug("Started prometheus server.")

    logging_thread = threading.Thread(
        target=log_from_subprocess, args=(proc,), daemon=True
    )
    logging_thread.start()

    logger.debug("Started thread to capture log output from prometheus server.")
    assert proc.poll() is None
    return proc


def setup_prom_exporter_server():
    """Start the prometheus exporter server that will expose the sensor data
    via the python prometheus client"""
    logger = logging.getLogger("air_qual_measure")
    logger.debug("Starting prometheus exporter server...")
    start_http_server(SERVER_PORT)
    logger.debug("Exporter server started.")


def celsius_to_fahrenheit(temp_c):
    """Convert celsius to fahrenheit."""
    return (temp_c * (9.0 / 5.0)) + 32.0


def check_status(endpoint, log=True):
    """Check on the response from the main prometheus server and return True if
    we got a good status code"""
    try:
        response = requests.get(endpoint)
        return 200 <= response.status_code < 300
    except requests.exceptions.RequestException:
        if log:
            logger = logging.getLogger("air_qual_measure")
            logger.exception("Exception occurred requesting %s", endpoint)
        return False


def check_and_restart_main_prometheus_server_if_needed(proc):
    """Determine if the main prometheus server died, and restart it if so."""
    bad_response = not check_status("http://localhost:9090/status")
    needs_restart = False
    logger = logging.getLogger("air_qual_measure")

    if proc is None and bad_response:
        logger.error(
            "Main prometheus server appears to have died, but we didn't start it originally."
            " Attempting to start it up."
        )
        needs_restart = True

    elif bad_response and proc is not None and proc.poll() is None:
        logger.error(
            "Main prometheus server returned a bad response, but it is still running."
            " Attempting to kill it and restart it."
        )
        proc.terminate()
        proc.wait(timeout=30)
        needs_restart = True

    elif proc is not None and proc.poll() is not None:
        logger.error("Main prometheus server died, restarting!")
        needs_restart = True

    if needs_restart:
        return start_main_prometheus_server()
    return proc


def check_and_restart_exporter_server_if_needed():
    """Determine if the prometheus exporter died, and restart it if so."""
    bad_response = not check_status("http://localhost:8090")
    if bad_response:
        logger = logging.getLogger("air_qual_measure")
        logger.error("prometheus exporter server died, restarting!")
        setup_prom_exporter_server()


def check_on_procs(prom_proc):
    """Check on the main prometheus server and the prometheus exporter server
    every 30s in a background thread, and restart them if they ended.
    """
    prom_proc = check_and_restart_main_prometheus_server_if_needed(prom_proc)
    check_and_restart_exporter_server_if_needed()

    checking_thread = threading.Timer(30.0, check_on_procs, args=(prom_proc,))
    checking_thread.daemon = True
    checking_thread.start()


def main():
    """Main entrypoint for the script."""
    logger = setup_logger()
    logger.info("Starting air quality measurements script.")
    scd_board, pm25_board = initialize_sensors()

    proc = None
    if not check_status("http://localhost:9090/status", log=False):
        proc = start_main_prometheus_server()
    setup_prom_exporter_server()
    checking_thread = threading.Timer(30.0, check_on_procs, args=(proc,))
    checking_thread.daemon = True
    checking_thread.start()

    co2_gauge = Gauge("sensor_co2_ppm", "CO2 PPM at a point in time.")
    temp_gauge = Gauge(
        "sensor_temperature_f", "Temperature in Fahrenheit at a point in time."
    )
    relative_humidity_gauge = Gauge(
        "sensor_relative_humidity_percent",
        "Relative humidity percent at a point in time.",
    )

    pm10_gauge = Gauge("sensor_pm10_ug_per_m3", "PM1.0 ug/m^3")
    pm25_gauge = Gauge("sensor_pm25_ug_per_m3", "PM2.5 ug/m^3")
    pm100_gauge = Gauge("sensor_pm100_ug_per_m3", "PM10.0 ug/m^3")
    p_03_gauge = Gauge(
        "sensor_p_03_num_per_decileter",
        "Number of particles with diameter beyond 0.3 um in 0.1 L of air.",
    )
    p_05_gauge = Gauge(
        "sensor_p_05_num_per_decileter",
        "Number of particles with diameter beyond 0.5 um in 0.1 L of air.",
    )
    p_10_gauge = Gauge(
        "sensor_p_10_num_per_decileter",
        "Number of particles with diameter beyond 1.0 um in 0.1 L of air.",
    )
    p_25_gauge = Gauge(
        "sensor_p_25_num_per_decileter",
        "Number of particles with diameter beyond 2.5 um in 0.1 L of air.",
    )
    p_50_gauge = Gauge(
        "sensor_p_50_num_per_decileter",
        "Number of particles with diameter beyond 5.0 um in 0.1 L of air.",
    )
    p_100_gauge = Gauge(
        "sensor_p_100_num_per_decileter",
        "Number of particles with diameter beyond 10.0 um in 0.1 L of air.",
    )

    logger.info("Entering main loop to read sensor data.")
    while True:
        time.sleep(1)

        data = read_from_scd(scd_board)

        if data is not None:
            co2, temp, rel_humidity = data

            if co2 > 0:
                co2_gauge.set(co2)

            if temp > 0:
                temp_gauge.set(celsius_to_fahrenheit(temp))

            if rel_humidity > 0:
                relative_humidity_gauge.set(rel_humidity)

        data = read_from_pm25(pm25_board)

        if data is not None:
            pm10, pm25, pm100, p_03, p_05, p_10, p_25, p_50, p_100 = data
            pm10_gauge.set(pm10)
            pm25_gauge.set(pm25)
            pm100_gauge.set(pm100)
            p_03_gauge.set(p_03)
            p_05_gauge.set(p_05)
            p_10_gauge.set(p_10)
            p_25_gauge.set(p_25)
            p_50_gauge.set(p_50)
            p_100_gauge.set(p_100)


if __name__ == "__main__":
    main()
