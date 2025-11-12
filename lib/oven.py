import threading
import time
import datetime
import logging
import json
import config
import os
import digitalio
import busio
import adafruit_bitbangio as bitbangio
import statistics
import tempfile
try:
    import fcntl
except ImportError:
    # fcntl not available on Windows
    fcntl = None

log = logging.getLogger(__name__)

class DupFilter(object):
    def __init__(self):
        self.msgs = set()

    def filter(self, record):
        rv = record.msg not in self.msgs
        self.msgs.add(record.msg)
        return rv

class Duplogger():
    def __init__(self):
        self.log = logging.getLogger("%s.dupfree" % (__name__))
        dup_filter = DupFilter()
        self.log.addFilter(dup_filter)
    def logref(self):
        return self.log

duplog = Duplogger().logref()

class Output(object):
    '''This represents a GPIO output that controls a solid
    state relay to turn the kiln elements on and off.
    inputs
        config.gpio_heat
        config.gpio_heat_invert
    '''
    def __init__(self):
        self.active = False
        self.heater = digitalio.DigitalInOut(config.gpio_heat) 
        self.heater.direction = digitalio.Direction.OUTPUT 
        self.off = config.gpio_heat_invert
        self.on = not self.off

    def heat(self,sleepfor):
        self.heater.value = self.on
        time.sleep(sleepfor)

    def cool(self,sleepfor):
        '''no active cooling, so sleep'''
        self.heater.value = self.off
        time.sleep(sleepfor)

# wrapper for blinka board
class Board(object):
    '''This represents a blinka board where this code
    runs.
    '''
    def __init__(self):
        log.info("board: %s" % (self.name))
        self.temp_sensor.start()

class RealBoard(Board):
    '''Each board has a thermocouple board attached to it.
    Any blinka board that supports SPI can be used. The
    board is automatically detected by blinka.
    '''
    def __init__(self):
        self.name = None
        self.load_libs()
        self.temp_sensor = self.choose_tempsensor()
        Board.__init__(self) 

    def load_libs(self):
        import board
        self.name = board.board_id

    def choose_tempsensor(self):
        if config.max31855:
            return Max31855()
        if config.max31856:
            return Max31856()

class SimulatedBoard(Board):
    '''Simulated board used during simulations.
    See config.simulate
    '''
    def __init__(self):
        self.name = "simulated"
        self.temp_sensor = TempSensorSimulated()
        Board.__init__(self) 

class TempSensor(threading.Thread):
    '''Used by the Board class. Each Board must have
    a TempSensor.
    '''
    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        self.time_step = config.sensor_time_wait
        self.status = ThermocoupleTracker()

class TempSensorSimulated(TempSensor):
    '''Simulates a temperature sensor '''
    def __init__(self):
        TempSensor.__init__(self)
        self.simulated_temperature = config.sim_t_env
    def temperature(self):
        return self.simulated_temperature

class TempSensorReal(TempSensor):
    '''real temperature sensor that takes many measurements
       during the time_step
       inputs
           config.temperature_average_samples 
    '''
    def __init__(self):
        TempSensor.__init__(self)
        self.sleeptime = self.time_step / float(config.temperature_average_samples)
        self.temptracker = TempTracker() 
        self.spi_setup()
        self.cs = digitalio.DigitalInOut(config.spi_cs)

    def spi_setup(self):
        if(hasattr(config,'spi_sclk') and
           hasattr(config,'spi_mosi') and
           hasattr(config,'spi_miso')):
            self.spi = bitbangio.SPI(config.spi_sclk, config.spi_mosi, config.spi_miso)
            log.info("Software SPI selected for reading thermocouple")
        else:
            import board
            self.spi = board.SPI();
            log.info("Hardware SPI selected for reading thermocouple")

    def get_temperature(self):
        '''read temp from tc and convert if needed'''
        try:
            temp = self.raw_temp() # raw_temp provided by subclasses
            if config.temp_scale.lower() == "f":
                temp = (temp*9/5)+32
            self.status.good()
            return temp
        except ThermocoupleError as tce:
            if tce.ignore:
                log.error("Problem reading temp (ignored) %s" % (tce.message))
                self.status.good()
            else:
                log.error("Problem reading temp %s" % (tce.message))
                self.status.bad()
        return None

    def temperature(self):
        '''average temp over a duty cycle'''
        return self.temptracker.get_avg_temp()

    def run(self):
        while True:
            temp = self.get_temperature()
            if temp:
                self.temptracker.add(temp)
            time.sleep(self.sleeptime)

class TempTracker(object):
    '''creates a sliding window of N temperatures per
       config.sensor_time_wait
    '''
    def __init__(self):
        self.size = config.temperature_average_samples
        self.temps = [0 for i in range(self.size)]
  
    def add(self,temp):
        self.temps.append(temp)
        while(len(self.temps) > self.size):
            del self.temps[0]

    def get_avg_temp(self, chop=25):
        '''
        take the median of the given values. this used to take an avg
        after getting rid of outliers. median works better.
        '''
        return statistics.median(self.temps)

class ThermocoupleTracker(object):
    '''Keeps sliding window to track successful/failed calls to get temp
       over the last two duty cycles.
    '''
    def __init__(self):
        self.size = config.temperature_average_samples * 2 
        self.status = [True for i in range(self.size)]
        self.limit = 30

    def good(self):
        '''True is good!'''
        self.status.append(True)
        del self.status[0]

    def bad(self):
        '''False is bad!'''
        self.status.append(False)
        del self.status[0]

    def error_percent(self):
        errors = sum(i == False for i in self.status) 
        return (errors/self.size)*100

    def over_error_limit(self):
        if self.error_percent() > self.limit:
            return True
        return False

class Max31855(TempSensorReal):
    '''each subclass expected to handle errors and get temperature'''
    def __init__(self):
        TempSensorReal.__init__(self)
        log.info("thermocouple MAX31855")
        import adafruit_max31855
        self.thermocouple = adafruit_max31855.MAX31855(self.spi, self.cs)

    def raw_temp(self):
        try:
            return self.thermocouple.temperature_NIST
        except RuntimeError as rte:
            if rte.args and rte.args[0]:
                raise Max31855_Error(rte.args[0])
            raise Max31855_Error('unknown')

class ThermocoupleError(Exception):
    '''
    thermocouple exception parent class to handle mapping of error messages
    and make them consistent across adafruit libraries. Also set whether
    each exception should be ignored based on settings in config.py.
    '''
    def __init__(self, message):
        self.ignore = False
        self.message = message
        self.map_message()
        self.set_ignore()
        super().__init__(self.message)

    def set_ignore(self):
        if self.message == "not connected" and config.ignore_tc_lost_connection == True:
            self.ignore = True
        if self.message == "short circuit" and config.ignore_tc_short_errors == True:
            self.ignore = True
        if self.message == "unknown" and config.ignore_tc_unknown_error == True:
            self.ignore = True
        if self.message == "cold junction range fault" and config.ignore_tc_cold_junction_range_error == True:
            self.ignore = True
        if self.message == "thermocouple range fault" and config.ignore_tc_range_error == True:
            self.ignore = True
        if self.message == "cold junction temp too high" and config.ignore_tc_cold_junction_temp_high == True:
            self.ignore = True
        if self.message == "cold junction temp too low" and config.ignore_tc_cold_junction_temp_low == True:
            self.ignore = True
        if self.message == "thermocouple temp too high" and config.ignore_tc_temp_high == True:
            self.ignore = True
        if self.message == "thermocouple temp too low" and config.ignore_tc_temp_low == True:
            self.ignore = True
        if self.message == "voltage too high or low" and config.ignore_tc_voltage_error == True:
            self.ignore = True

    def map_message(self):
        try:
            self.message = self.map[self.orig_message]
        except KeyError:
            self.message = "unknown"

class Max31855_Error(ThermocoupleError):
    '''
    All children must set self.orig_message and self.map
    '''
    def __init__(self, message):
        self.orig_message = message
        # this purposefully makes "fault reading" and
        # "Total thermoelectric voltage out of range..." unknown errors
        self.map = {
            "thermocouple not connected" : "not connected",
            "short circuit to ground" : "short circuit",
            "short circuit to power" : "short circuit",
            }
        super().__init__(message)

class Max31856_Error(ThermocoupleError):
    def __init__(self, message):
        self.orig_message = message
        self.map = {
            "cj_range" : "cold junction range fault",
            "tc_range" : "thermocouple range fault",
            "cj_high"  : "cold junction temp too high",
            "cj_low"   : "cold junction temp too low",
            "tc_high"  : "thermocouple temp too high",
            "tc_low"   : "thermocouple temp too low",
            "voltage"  : "voltage too high or low", 
            "open_tc"  : "not connected"
            }
        super().__init__(message)

class Max31856(TempSensorReal):
    '''each subclass expected to handle errors and get temperature'''
    def __init__(self):
        TempSensorReal.__init__(self)
        log.info("thermocouple MAX31856")
        import adafruit_max31856
        self.thermocouple = adafruit_max31856.MAX31856(self.spi,self.cs,
                                        thermocouple_type=config.thermocouple_type)
        if (config.ac_freq_50hz == True):
            self.thermocouple.noise_rejection = 50
        else:
            self.thermocouple.noise_rejection = 60

    def raw_temp(self):
        # The underlying adafruit library does not throw exceptions
        # for thermocouple errors. Instead, they are stored in 
        # dict named self.thermocouple.fault. Here we check that
        # dict for errors and raise an exception.
        # and raise Max31856_Error(message)
        temp = self.thermocouple.temperature
        for k,v in self.thermocouple.fault.items():
            if v:
                raise Max31856_Error(k)
        return temp

class Oven(threading.Thread):
    '''parent oven class. this has all the common code
       for either a real or simulated oven'''
    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        self.temperature = 0
        self.time_step = config.sensor_time_wait
        self.reset()

    def reset(self):
        self.cost = 0
        self.state = "IDLE"
        self.profile = None
        self.start_time = 0
        self.runtime = 0
        self.totaltime = 0
        self.target = 0
        self.heat = 0
        self.heat_rate = 0
        self.heat_rate_temps = []
        self.pid = PID(ki=config.pid_ki, kd=config.pid_kd, kp=config.pid_kp)
        self.catching_up = False
        self.divergence_samples = []  # Track temp divergence for firing log
        # Cooling estimation variables
        self.cooling_mode = False
        self.cooling_temps = []  # List of (timestamp, temperature) tuples
        self.cooling_estimate = None  # Estimated time remaining (HH:MM string or None)
        self.last_k_calculation_time = 0  # Track when we last calculated k

    @staticmethod
    def get_start_from_temperature(profile, temp):
        target_temp = profile.get_target_temperature(0)
        if temp > target_temp + 5:
            startat = profile.find_next_time_from_temperature(temp)
            log.info("seek_start is in effect, starting at: {} s, {} deg".format(round(startat), round(temp)))
        else:
            startat = 0
        return startat

    def set_heat_rate(self,runtime,temp):
        '''heat rate is the heating rate in degrees/hour
        '''
        # arbitrary number of samples
        # the time this covers changes based on a few things
        numtemps = 60
        self.heat_rate_temps.append((runtime,temp))
         
        # drop old temps off the list
        if len(self.heat_rate_temps) > numtemps:
            self.heat_rate_temps = self.heat_rate_temps[-1*numtemps:]
        time2 = self.heat_rate_temps[-1][0]
        time1 = self.heat_rate_temps[0][0]
        temp2 = self.heat_rate_temps[-1][1]
        temp1 = self.heat_rate_temps[0][1]
        if time2 > time1:
            self.heat_rate = ((temp2 - temp1) / (time2 - time1))*3600

    def run_profile(self, profile, startat=0, allow_seek=True):
        log.debug('run_profile run on thread' + threading.current_thread().name)
        runtime = startat * 60
        if allow_seek:
            if self.state == 'IDLE':
                if config.seek_start:
                    temp = self.board.temp_sensor.temperature()  # Defined in a subclass
                    runtime += self.get_start_from_temperature(profile, temp)

        self.reset()
        self.startat = startat * 60
        self.runtime = runtime
        self.start_time = datetime.datetime.now() - datetime.timedelta(seconds=self.startat)
        self.profile = profile
        self.totaltime = profile.get_duration()
        self.state = "RUNNING"
        log.info("Running schedule %s starting at %d minutes" % (profile.name,startat))
        log.info("Starting")

    def abort_run(self):
        # Save firing log if we were running
        if self.profile:
            self.save_firing_log(status="aborted")
        self.reset()
        self.save_automatic_restart_state()

    def get_start_time(self):
        return datetime.datetime.now() - datetime.timedelta(milliseconds = self.runtime * 1000)

    def kiln_must_catch_up(self):
        '''shift the whole schedule forward in time by one time_step
        to wait for the kiln to catch up'''
        if config.kiln_must_catch_up == True:
            temp = self.board.temp_sensor.temperature() + \
                config.thermocouple_offset
            # kiln too cold, wait for it to heat up
            if self.target - temp > config.pid_control_window:
                log.info("kiln must catch up, too cold, shifting schedule")
                self.start_time = self.get_start_time()
                self.catching_up = True;
                return
            # kiln too hot, wait for it to cool down
            if temp - self.target > config.pid_control_window:
                log.info("kiln must catch up, too hot, shifting schedule")
                self.start_time = self.get_start_time()
                self.catching_up = True;
                return
            self.catching_up = False;

    def update_runtime(self):

        runtime_delta = datetime.datetime.now() - self.start_time
        if runtime_delta.total_seconds() < 0:
            runtime_delta = datetime.timedelta(0)

        self.runtime = runtime_delta.total_seconds()

    def update_target_temp(self):
        self.target = self.profile.get_target_temperature(self.runtime)

    def reset_if_emergency(self):
        '''reset if the temperature is way TOO HOT, or other critical errors detected'''
        if (self.board.temp_sensor.temperature() + config.thermocouple_offset >=
            config.emergency_shutoff_temp):
            log.info("emergency!!! temperature too high")
            if config.ignore_temp_too_high == False:
                # Save firing log as emergency before aborting
                if self.profile:
                    self.save_firing_log(status="emergency_stop")
                self.reset()
                self.save_automatic_restart_state()
                return
        
        if self.board.temp_sensor.status.over_error_limit():
            log.info("emergency!!! too many errors in a short period")
            if config.ignore_tc_too_many_errors == False:
                # Save firing log as emergency before aborting
                if self.profile:
                    self.save_firing_log(status="emergency_stop")
                self.reset()
                self.save_automatic_restart_state()

    def reset_if_schedule_ended(self):
        if self.runtime > self.totaltime:
            log.info("schedule ended, shutting down")
            log.info("total cost = %s%.2f" % (config.currency_type,self.cost))
            # Save firing log as completed before transitioning to cooling
            self.save_firing_log(status="completed")
            # Transition to cooling mode instead of immediate reset
            self.start_cooling()
            self.state = "IDLE"
            self.save_automatic_restart_state()

    def update_cost(self):
        if self.heat:
            cost = (config.kwh_rate * config.kw_elements) * (self.time_step/3600)
        else:
            cost = 0
        self.cost = self.cost + cost
    
    def track_divergence(self):
        """Track temperature divergence (actual vs target) for firing log analysis"""
        try:
            temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
            divergence = abs(self.target - temp)
            self.divergence_samples.append(divergence)
        except (AttributeError, TypeError):
            # Handle cases where temp sensor isn't ready
            pass
    
    def calculate_avg_divergence(self):
        """Calculate average temperature divergence over the entire firing"""
        if not self.divergence_samples:
            return 0.0
        return sum(self.divergence_samples) / len(self.divergence_samples)

    def start_cooling(self):
        """Initialize cooling mode after firing schedule completes"""
        self.cooling_mode = True
        self.cooling_temps = []
        self.cooling_estimate = None
        self.last_k_calculation_time = time.time()
        log.info("Cooling mode activated - tracking temperature for estimate")

    def calculate_cooling_constant(self):
        """
        Calculate cooling constant k using Newton's Law of Cooling
        T(t) = T_ambient + (T_initial - T_ambient) * e^(-k*t)
        
        Using curve fitting on recent temperature samples to determine k.
        Returns k value or None if insufficient data.
        """
        import math
        
        if len(self.cooling_temps) < config.cooling_min_samples:
            return None
        
        # Get ambient temperature in current temp scale
        ambient_temp = config.cooling_ambient_temp
        if config.temp_scale.lower() == "c":
            # Convert from F to C
            ambient_temp = (ambient_temp - 32) * 5 / 9
        
        # Use linear regression on ln((T - T_ambient) / (T0 - T_ambient)) = -k*t
        # to find k
        try:
            t0 = self.cooling_temps[0][0]
            T0 = self.cooling_temps[0][1]
            
            # Check if already close to ambient (not enough delta to measure)
            if abs(T0 - ambient_temp) < 10:
                log.debug("Temperature too close to ambient for accurate k calculation")
                return None
            
            # Prepare data for linear regression
            x_values = []  # time differences
            y_values = []  # ln((T - T_ambient) / (T0 - T_ambient))
            
            for timestamp, temp in self.cooling_temps:
                delta_t = timestamp - t0
                temp_diff = temp - ambient_temp
                initial_diff = T0 - ambient_temp
                
                # Skip if temperature difference is too small or negative
                if temp_diff <= 0 or initial_diff <= 0:
                    continue
                
                ratio = temp_diff / initial_diff
                if ratio <= 0:
                    continue
                
                x_values.append(delta_t)
                y_values.append(math.log(ratio))
            
            if len(x_values) < config.cooling_min_samples:
                return None
            
            # Linear regression: y = mx + b, where m = -k
            n = len(x_values)
            sum_x = sum(x_values)
            sum_y = sum(y_values)
            sum_xx = sum(x * x for x in x_values)
            sum_xy = sum(x * y for x, y in zip(x_values, y_values))
            
            denominator = (n * sum_xx - sum_x * sum_x)
            if abs(denominator) < 1e-10:
                return None
            
            slope = (n * sum_xy - sum_x * sum_y) / denominator
            k = -slope  # k is the negative of the slope
            
            # Sanity check: k should be positive and reasonable
            if k <= 0 or k > 1:  # k > 1 would mean cooling in < 1 second, unrealistic
                log.debug("Calculated k=%f is out of reasonable range" % k)
                return None
            
            log.debug("Calculated cooling constant k=%f" % k)
            return k
            
        except (ValueError, ZeroDivisionError, OverflowError) as e:
            log.error("Error calculating cooling constant: %s" % e)
            return None

    def estimate_time_to_target(self, current_temp, k):
        """
        Calculate estimated time (in seconds) to reach target temperature
        using Newton's Law of Cooling: T(t) = T_ambient + (T_current - T_ambient) * e^(-k*t)
        
        Solving for t: t = -ln((T_target - T_ambient) / (T_current - T_ambient)) / k
        """
        import math
        
        # Get temperatures in current temp scale
        target_temp = config.cooling_target_temp
        ambient_temp = config.cooling_ambient_temp
        
        if config.temp_scale.lower() == "c":
            # Convert from F to C
            target_temp = (target_temp - 32) * 5 / 9
            ambient_temp = (ambient_temp - 32) * 5 / 9
        
        # Check if already at or below target
        if current_temp <= target_temp:
            return 0
        
        # Calculate time
        try:
            numerator = target_temp - ambient_temp
            denominator = current_temp - ambient_temp
            
            if denominator <= 0 or numerator <= 0:
                return None
            
            ratio = numerator / denominator
            if ratio <= 0 or ratio > 1:
                return None
            
            time_seconds = -math.log(ratio) / k
            
            # Sanity check: shouldn't be negative or unreasonably large
            if time_seconds < 0 or time_seconds > 86400 * 7:  # 7 days max
                return None
            
            return time_seconds
            
        except (ValueError, ZeroDivisionError, OverflowError) as e:
            log.error("Error estimating time to target: %s" % e)
            return None

    def format_cooling_time(self, seconds):
        """Convert seconds to HH:MM format"""
        if seconds is None or seconds < 0:
            return None
        
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        
        return "%02d:%02d" % (hours, minutes)

    def update_cooling_estimate(self):
        """Update cooling estimate based on recent temperature readings"""
        try:
            # Get current temperature
            current_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
            current_time = time.time()
            
            # Add current temperature to tracking list
            self.cooling_temps.append((current_time, current_temp))
            
            # Keep only recent samples (last 30 minutes worth)
            max_samples = 900  # 30 min * 60 sec / 2 sec per sample
            if len(self.cooling_temps) > max_samples:
                self.cooling_temps = self.cooling_temps[-max_samples:]
            
            # Check if temperature is already below target
            target_temp = config.cooling_target_temp
            if config.temp_scale.lower() == "c":
                target_temp = (target_temp - 32) * 5 / 9
            
            if current_temp <= target_temp:
                self.cooling_estimate = "Ready"
                return
            
            # Calculate or recalculate k every 2-3 minutes
            recalc_interval = 150  # 2.5 minutes
            if (current_time - self.last_k_calculation_time) >= recalc_interval:
                k = self.calculate_cooling_constant()
                if k is not None:
                    # Estimate time to target
                    time_remaining = self.estimate_time_to_target(current_temp, k)
                    if time_remaining is not None:
                        self.cooling_estimate = self.format_cooling_time(time_remaining)
                        self.last_k_calculation_time = current_time
                    else:
                        self.cooling_estimate = "Calculating..."
                else:
                    self.cooling_estimate = "Calculating..."
            # If we haven't recalculated yet, keep the previous estimate or show "Calculating..."
            elif self.cooling_estimate is None:
                self.cooling_estimate = "Calculating..."
                
        except (AttributeError, TypeError) as e:
            log.error("Error updating cooling estimate: %s" % e)
            self.cooling_estimate = None

    def get_state(self):
        temp = 0
        try:
            temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        except AttributeError as error:
            # this happens at start-up with a simulated oven
            temp = 0
            pass

        self.set_heat_rate(self.runtime,temp)

        state = {
            'cost': self.cost,
            'runtime': self.runtime,
            'temperature': temp,
            'target': self.target,
            'state': self.state,
            'heat': self.heat,
            'heat_rate': self.heat_rate,
            'totaltime': self.totaltime,
            'kwh_rate': config.kwh_rate,
            'currency_type': config.currency_type,
            'profile': self.profile.name if self.profile else None,
            'pidstats': self.pid.pidstats,
            'catching_up': self.catching_up,
            'door': 'CLOSED',
            'cooling_estimate': self.cooling_estimate if self.cooling_mode else None,
        }
        return state

    def save_state(self):
        """Save state to file with atomic write"""
        try:
            # Write to temporary file in same directory (ensures same filesystem)
            temp_fd, temp_path = tempfile.mkstemp(
                dir=os.path.dirname(config.automatic_restart_state_file),
                prefix='.tmp_state_',
                suffix='.json'
            )
            
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                # Optional: Lock the file for exclusive access
                if fcntl is not None:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    except (AttributeError, OSError):
                        pass
                
                try:
                    json.dump(self.get_state(), f, ensure_ascii=False, indent=4)
                    f.flush()
                    os.fsync(f.fileno())  # Force write to disk
                finally:
                    if fcntl is not None:
                        try:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                        except (AttributeError, OSError):
                            pass
            
            # Atomic rename (overwrites old file if exists)
            # On POSIX systems, this is atomic
            os.replace(temp_path, config.automatic_restart_state_file)
            
        except Exception as e:
            log.error("Failed to save state: %s" % e)
            # Clean up temp file if it exists
            try:
                if 'temp_path' in locals() and os.path.exists(temp_path):
                    os.unlink(temp_path)
            except:
                pass

    def state_file_is_old(self):
        '''returns True is state files is older than 15 mins default
                   False if younger
                   True if state file cannot be opened or does not exist
        '''
        if os.path.isfile(config.automatic_restart_state_file):
            state_age = os.path.getmtime(config.automatic_restart_state_file)
            now = time.time()
            minutes = (now - state_age)/60
            if(minutes <= config.automatic_restart_window):
                return False
        return True

    def save_automatic_restart_state(self):
        # only save state if the feature is enabled
        if not config.automatic_restarts == True:
            return False
        self.save_state()
    
    def save_firing_log(self, status="completed"):
        """Save a complete firing log to disk with temperature data and statistics"""
        if not self.profile:
            log.info("No profile to save - firing log not created")
            return False
        
        try:
            # Get temperature log from ovenwatcher
            temp_log = []
            if hasattr(self, 'ovenwatcher') and self.ovenwatcher.last_log:
                # Subsample to reasonable size (max 500 points)
                temp_log = self.ovenwatcher.lastlog_subset(maxpts=500)
                # Extract only needed fields
                temp_log = [{
                    'runtime': round(entry.get('runtime', 0), 2),
                    'temperature': round(entry.get('temperature', 0), 2),
                    'target': round(entry.get('target', 0), 2)
                } for entry in temp_log]
            
            # Calculate statistics
            avg_divergence = self.calculate_avg_divergence()
            
            # Get final temperature
            final_temp = 0
            try:
                final_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
            except (AttributeError, TypeError):
                pass
            
            # Create firing log data structure
            firing_log = {
                'profile_name': self.profile.name,
                'start_time': self.start_time.isoformat() if self.start_time else None,
                'end_time': datetime.datetime.now().isoformat(),
                'duration_seconds': int(self.runtime),
                'final_cost': round(self.cost, 2),
                'final_temperature': round(final_temp, 2),
                'avg_divergence': round(avg_divergence, 2),
                'currency_type': config.currency_type,
                'temp_scale': config.temp_scale,
                'status': status,
                'temperature_log': temp_log
            }
            
            # Save to firing logs directory
            os.makedirs(config.firing_logs_directory, exist_ok=True)
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            safe_profile_name = "".join(c for c in self.profile.name if c.isalnum() or c in (' ', '-', '_')).strip()
            filename = f"{timestamp}_{safe_profile_name}.json"
            filepath = os.path.join(config.firing_logs_directory, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(firing_log, f, ensure_ascii=False, indent=2)
            
            log.info(f"Firing log saved: {filepath}")
            
            # Also save as "last firing" summary
            last_firing_summary = {
                'profile_name': self.profile.name,
                'end_time': firing_log['end_time'],
                'duration_seconds': firing_log['duration_seconds'],
                'final_cost': firing_log['final_cost'],
                'avg_divergence': firing_log['avg_divergence'],
                'currency_type': config.currency_type,
                'temp_scale': config.temp_scale,
                'status': status,
                'log_filename': filename
            }
            
            with open(config.last_firing_file, 'w', encoding='utf-8') as f:
                json.dump(last_firing_summary, f, ensure_ascii=False, indent=2)
            
            log.info(f"Last firing summary saved: {config.last_firing_file}")
            return True
            
        except Exception as e:
            log.error(f"Failed to save firing log: {e}")
            return False

    def should_i_automatic_restart(self):
        """Read state file with error handling"""
        # only automatic restart if the feature is enabled
        if not config.automatic_restarts == True:
            return False
        if self.state_file_is_old():
            duplog.info("automatic restart not possible. state file does not exist or is too old.")
            return False

        try:
            with open(config.automatic_restart_state_file, 'r') as infile:
                # Optional: Shared read lock
                if fcntl is not None:
                    try:
                        fcntl.flock(infile.fileno(), fcntl.LOCK_SH)
                    except (AttributeError, OSError):
                        pass
                
                try:
                    d = json.load(infile)
                finally:
                    if fcntl is not None:
                        try:
                            fcntl.flock(infile.fileno(), fcntl.LOCK_UN)
                        except (AttributeError, OSError):
                            pass
            
            if d.get("state") != "RUNNING":
                duplog.info("automatic restart not possible. state = %s" % d.get("state"))
                return False
            
            return True
            
        except (IOError, ValueError, json.JSONDecodeError) as e:
            log.error("Failed to read state file: %s" % e)
            return False
        except Exception as e:
            log.error("Unexpected error reading state file: %s" % e)
            return False

    def automatic_restart(self):
        with open(config.automatic_restart_state_file) as infile: d = json.load(infile)
        startat = d["runtime"]/60
        filename = "%s.json" % (d["profile"])
        profile_path = os.path.abspath(os.path.join(os.path.dirname( __file__ ), '..', 'storage','profiles',filename))

        log.info("automatically restarting profile = %s at minute = %d" % (profile_path,startat))
        with open(profile_path) as infile:
            profile_json = json.dumps(json.load(infile))
        profile = Profile(profile_json)
        self.run_profile(profile, startat=startat, allow_seek=False)  # We don't want a seek on an auto restart.
        self.cost = d["cost"]
        time.sleep(1)
        self.ovenwatcher.record(profile)

    def set_ovenwatcher(self,watcher):
        log.info("ovenwatcher set in oven class")
        self.ovenwatcher = watcher

    def run(self):
        while True:
            log.debug('Oven running on ' + threading.current_thread().name)
            if self.state == "IDLE":
                if self.should_i_automatic_restart() == True:
                    self.automatic_restart()
                else:
                    # Check if we should activate cooling mode
                    try:
                        current_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
                        target_temp = config.cooling_target_temp
                        if config.temp_scale.lower() == "c":
                            target_temp = (target_temp - 32) * 5 / 9
                        
                        # Activate cooling mode if above target temp
                        if current_temp > target_temp:
                            if not self.cooling_mode:
                                self.start_cooling()
                            self.update_cooling_estimate()
                        else:
                            # Below target temp, disable cooling mode
                            if self.cooling_mode:
                                self.cooling_mode = False
                                self.cooling_estimate = None
                    except (AttributeError, TypeError):
                        pass
                        
                time.sleep(1)
                continue
            if self.state == "PAUSED":
                self.start_time = self.get_start_time()
                self.update_runtime()
                self.update_target_temp()
                self.heat_then_cool()
                self.reset_if_emergency()
                self.reset_if_schedule_ended()
                continue
            if self.state == "RUNNING":
                self.update_cost()
                self.track_divergence()
                self.save_automatic_restart_state()
                self.kiln_must_catch_up()
                self.update_runtime()
                self.update_target_temp()
                self.heat_then_cool()
                self.reset_if_emergency()
                self.reset_if_schedule_ended()

class SimulatedOven(Oven):

    def __init__(self):
        self.board = SimulatedBoard()
        self.t_env = config.sim_t_env
        self.c_heat = config.sim_c_heat
        self.c_oven = config.sim_c_oven
        self.p_heat = config.sim_p_heat
        self.R_o_nocool = config.sim_R_o_nocool
        self.R_ho_noair = config.sim_R_ho_noair
        self.R_ho = self.R_ho_noair
        self.speedup_factor = config.sim_speedup_factor

        # set temps to the temp of the surrounding environment
        self.t = config.sim_t_env  # deg C or F temp of oven
        self.t_h = self.t_env #deg C temp of heating element

        super().__init__()

        self.start_time = self.get_start_time();

        # start thread
        self.start()
        log.info("SimulatedOven started")

    # runtime is in sped up time, start_time is actual time of day
    def get_start_time(self):
        return datetime.datetime.now() - datetime.timedelta(milliseconds = self.runtime * 1000 / self.speedup_factor)

    def update_runtime(self):
        runtime_delta = datetime.datetime.now() - self.start_time
        if runtime_delta.total_seconds() < 0:
            runtime_delta = datetime.timedelta(0)

        self.runtime = runtime_delta.total_seconds() * self.speedup_factor

    def update_target_temp(self):
        self.target = self.profile.get_target_temperature(self.runtime)

    def heating_energy(self,pid):
        # using pid here simulates the element being on for
        # only part of the time_step
        self.Q_h = self.p_heat * self.time_step * pid

    def temp_changes(self):
        #temperature change of heat element by heating
        self.t_h += self.Q_h / self.c_heat

        #energy flux heat_el -> oven
        self.p_ho = (self.t_h - self.t) / self.R_ho

        #temperature change of oven and heating element
        self.t += self.p_ho * self.time_step / self.c_oven
        self.t_h -= self.p_ho * self.time_step / self.c_heat

        #temperature change of oven by cooling to environment
        self.p_env = (self.t - self.t_env) / self.R_o_nocool
        self.t -= self.p_env * self.time_step / self.c_oven
        self.temperature = self.t
        self.board.temp_sensor.simulated_temperature = self.t

    def heat_then_cool(self):
        now_simulator = self.start_time + datetime.timedelta(milliseconds = self.runtime * 1000)
        pid = self.pid.compute(self.target,
                               self.board.temp_sensor.temperature() +
                               config.thermocouple_offset, now_simulator)

        heat_on = float(self.time_step * pid)
        heat_off = float(self.time_step * (1 - pid))

        self.heating_energy(pid)
        self.temp_changes()

        # self.heat is for the front end to display if the heat is on
        self.heat = 0.0
        if heat_on > 0:
            self.heat = heat_on

        log.info("simulation: -> %dW heater: %.0f -> %dW oven: %.0f -> %dW env" % (int(self.p_heat * pid),
            self.t_h,
            int(self.p_ho),
            self.t,
            int(self.p_env)))

        time_left = self.totaltime - self.runtime

        try:
            log.info("temp=%.2f, target=%.2f, error=%.2f, pid=%.2f, p=%.2f, i=%.2f, d=%.2f, heat_on=%.2f, heat_off=%.2f, run_time=%d, total_time=%d, time_left=%d" %
                (self.pid.pidstats['ispoint'],
                self.pid.pidstats['setpoint'],
                self.pid.pidstats['err'],
                self.pid.pidstats['pid'],
                self.pid.pidstats['p'],
                self.pid.pidstats['i'],
                self.pid.pidstats['d'],
                heat_on,
                heat_off,
                self.runtime,
                self.totaltime,
                time_left))
        except KeyError:
            pass

        # we don't actually spend time heating & cooling during
        # a simulation, so sleep.
        time.sleep(self.time_step / self.speedup_factor)


class RealOven(Oven):

    def __init__(self):
        self.board = RealBoard()
        self.output = Output()
        self.reset()

        # call parent init
        Oven.__init__(self)

        # start thread
        self.start()

    def reset(self):
        super().reset()
        self.output.cool(0)

    def heat_then_cool(self):
        pid = self.pid.compute(self.target,
                               self.board.temp_sensor.temperature() +
                               config.thermocouple_offset, datetime.datetime.now())

        heat_on = float(self.time_step * pid)
        heat_off = float(self.time_step * (1 - pid))

        # self.heat is for the front end to display if the heat is on
        self.heat = 0.0
        if heat_on > 0:
            self.heat = 1.0

        if heat_on:
            self.output.heat(heat_on)
        if heat_off:
            self.output.cool(heat_off)
        time_left = self.totaltime - self.runtime
        try:
            log.info("temp=%.2f, target=%.2f, error=%.2f, pid=%.2f, p=%.2f, i=%.2f, d=%.2f, heat_on=%.2f, heat_off=%.2f, run_time=%d, total_time=%d, time_left=%d" %
                (self.pid.pidstats['ispoint'],
                self.pid.pidstats['setpoint'],
                self.pid.pidstats['err'],
                self.pid.pidstats['pid'],
                self.pid.pidstats['p'],
                self.pid.pidstats['i'],
                self.pid.pidstats['d'],
                heat_on,
                heat_off,
                self.runtime,
                self.totaltime,
                time_left))
        except KeyError:
            pass

class Profile():
    def __init__(self, json_data):
        obj = json.loads(json_data)
        self.name = obj["name"]
        self.data = sorted(obj["data"])

    def get_duration(self):
        return max([t for (t, x) in self.data])

    #  x = (y-y1)(x2-x1)/(y2-y1) + x1
    @staticmethod
    def find_x_given_y_on_line_from_two_points(y, point1, point2):
        """
        Find x (time) given y (temperature) on a line defined by two points.
        
        Returns:
            float: Time value if successful
            None: If points are invalid, slope is non-positive, or y is out of range
        """
        # Validate point order
        if point1[0] > point2[0]:
            log.debug("Points in wrong order: time2 before time1")
            return None
        
        # Validate temperature slope (must be increasing)
        if point1[1] >= point2[1]:
            log.debug("Flat or negative temperature slope, cannot seek")
            return None
        
        # Check if y is in range
        if y < point1[1] or y > point2[1]:
            log.debug("Temperature %s outside segment range [%s, %s]" % 
                      (y, point1[1], point2[1]))
            return None
        
        # Calculate x
        x = (y - point1[1]) * (point2[0] - point1[0]) / (point2[1] - point1[1]) + point1[0]
        return x

    def find_next_time_from_temperature(self, temperature):
        """Find the time when temperature is reached in the profile"""
        time = 0  # Default if no intersection found
        for index, point2 in enumerate(self.data):
            if point2[1] >= temperature:
                if index > 0:
                    if self.data[index - 1][1] <= temperature:
                        result = self.find_x_given_y_on_line_from_two_points(
                            temperature, self.data[index - 1], point2)
                        
                        # Check for None instead of 0
                        if result is not None:
                            time = result
                            break
                        elif self.data[index - 1][1] == point2[1]:
                            # Flat segment that matches temperature
                            time = self.data[index - 1][0]
                            break
                        # else: result is None (error), keep time=0
        return time

    def get_surrounding_points(self, time):
        if time > self.get_duration():
            return (None, None)
        
        # Handle time at or past final point
        if time >= self.data[-1][0]:
            if len(self.data) >= 2:
                return (self.data[-2], self.data[-1])
            else:
                # Single point profile
                return (self.data[0], self.data[0])

        prev_point = None
        next_point = None

        for i in range(len(self.data)):
            if time < self.data[i][0]:
                prev_point = self.data[i-1]
                next_point = self.data[i]
                break

        return (prev_point, next_point)

    def get_target_temperature(self, time):
        if time > self.get_duration():
            return 0

        (prev_point, next_point) = self.get_surrounding_points(time)
        
        # Defensive check - should never happen with above fix
        if prev_point is None or next_point is None:
            log.error("get_surrounding_points returned None for time=%s" % time)
            return 0
        
        # Handle identical points (flat segment at end)
        if next_point[0] == prev_point[0]:
            return prev_point[1]

        incl = float(next_point[1] - prev_point[1]) / float(next_point[0] - prev_point[0])
        temp = prev_point[1] + (time - prev_point[0]) * incl
        return temp


class PID():

    def __init__(self, ki=1, kp=1, kd=1):
        self.ki = ki
        self.kp = kp
        self.kd = kd
        self.lastNow = datetime.datetime.now()
        self.iterm = 0
        self.lastErr = 0
        self.pidstats = {}

    # FIX - this was using a really small window where the PID control
    # takes effect from -1 to 1. I changed this to various numbers and
    # settled on -50 to 50 and then divide by 50 at the end. This results
    # in a larger PID control window and much more accurate control...
    # instead of what used to be binary on/off control.
    def compute(self, setpoint, ispoint, now):
        timeDelta = (now - self.lastNow).total_seconds()

        window_size = 100

        error = float(setpoint - ispoint)

        # this removes the need for config.stop_integral_windup
        # it turns the controller into a binary on/off switch
        # any time it's outside the window defined by
        # config.pid_control_window
        icomp = 0
        output = 0
        out4logs = 0
        dErr = 0
        if error < (-1 * config.pid_control_window):
            log.info("kiln outside pid control window, max cooling")
            output = 0
            # it is possible to set self.iterm=0 here and also below
            # but I dont think its needed
        elif error > (1 * config.pid_control_window):
            log.info("kiln outside pid control window, max heating")
            output = 1
            if config.throttle_below_temp and config.throttle_percent:
                if setpoint <= config.throttle_below_temp:
                    output = config.throttle_percent/100
                    log.info("max heating throttled at %d percent below %d degrees to prevent overshoot" % (config.throttle_percent,config.throttle_below_temp))
        else:
            # Proportional term
            p_term = self.kp * error
            
            # Derivative term
            dErr = (error - self.lastErr) / timeDelta
            d_term = self.kd * dErr
            
            # Calculate integral contribution (but don't add to iterm yet)
            i_contribution = error * timeDelta * (1/self.ki)
            
            # Calculate output before clamping
            output_unclamped = p_term + self.iterm + d_term
            
            # Clamp output to limits
            output = sorted([-1 * window_size, output_unclamped, window_size])[1]
            
            # Anti-windup: Only accumulate integral if output is not saturated
            # This prevents integral windup during saturation
            if output_unclamped == output:
                # Output is not saturated, safe to accumulate
                self.iterm += i_contribution
            else:
                # Output is saturated, don't accumulate more integral
                log.debug("PID output saturated at %.2f, preventing integral windup" % output)
            
            out4logs = output
            output = float(output / window_size)
            
        self.lastErr = error
        self.lastNow = now

        # no active cooling
        if output < 0:
            output = 0

        self.pidstats = {
            'time': time.mktime(now.timetuple()),
            'timeDelta': timeDelta,
            'setpoint': setpoint,
            'ispoint': ispoint,
            'err': error,
            'errDelta': dErr,
            'p': self.kp * error,
            'i': self.iterm,
            'd': self.kd * dErr,
            'kp': self.kp,
            'ki': self.ki,
            'kd': self.kd,
            'pid': out4logs,
            'out': output,
        }

        return output
