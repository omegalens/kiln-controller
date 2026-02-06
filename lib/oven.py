import threading
import time
import datetime
import logging
import json
import config
import os
import statistics
import tempfile

# Hardware imports - wrapped for simulation mode on non-Pi systems
try:
    import digitalio
    import busio
    import adafruit_bitbangio as bitbangio
except (ImportError, NotImplementedError):
    digitalio = None
    busio = None
    bitbangio = None
    print("Hardware modules not available in oven.py - simulation mode only")

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
        # Use sim_initial_temp for starting temperature (allows testing pre-heated kiln)
        self.simulated_temperature = getattr(config, 'sim_initial_temp', config.sim_t_env)
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
        self.wall_clock_start_time = None     # Actual wall clock time when run started (no offset)
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
        
        # Safety & State Tracking
        self.last_state_save = 0
        self.stall_start_time = None
        self.runaway_start_time = None
        self.last_temp_check_time = 0
        self.last_temp_reading = 0

        # Segment-based control state (v2 profile format)
        self.current_segment_index = 0
        self.segment_phase = 'ramp'           # 'ramp' or 'hold'
        self.segment_start_time = None
        self.segment_start_temp = None
        self.hold_start_time = None
        self.actual_elapsed_time = 0          # Wall clock time since run started (no offset)
        self.schedule_progress = 0.0          # 0-100% based on temp progress
        self.target_heat_rate = 0             # The rate we're trying to achieve

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
        Uses a hybrid approach: keeps minimum samples OR time window, whichever retains more data.
        This ensures rate calculation works with both fast updates and high speedup factors.
        '''
        # Minimum samples to keep (ensures we have enough data points)
        min_samples = 10
        # Time window in seconds (default 5 minutes of simulated time)
        rate_window_seconds = getattr(config, 'heat_rate_window_seconds', 300)
        
        self.heat_rate_temps.append((runtime, temp))
        
        # Remove samples older than the time window, but always keep at least min_samples
        if len(self.heat_rate_temps) > min_samples:
            cutoff_time = runtime - rate_window_seconds
            # Filter by time, but don't go below min_samples
            filtered = [(t, tp) for t, tp in self.heat_rate_temps if t >= cutoff_time]
            if len(filtered) >= min_samples:
                self.heat_rate_temps = filtered
            else:
                # Keep the most recent min_samples
                self.heat_rate_temps = self.heat_rate_temps[-min_samples:]
        
        # Cap at 1000 samples to prevent memory issues
        if len(self.heat_rate_temps) > 1000:
            self.heat_rate_temps = self.heat_rate_temps[-1000:]
        
        if len(self.heat_rate_temps) >= 2:
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
        # startat includes seek_offset so that update_runtime() preserves the offset
        self.startat = runtime  # Includes both manual startat and seek_offset
        self.runtime = runtime
        self.start_time = datetime.datetime.now() - datetime.timedelta(seconds=self.startat)
        self.wall_clock_start_time = datetime.datetime.now()  # Actual wall clock start (no offset)
        self.profile = profile
        self.totaltime = profile.get_duration()
        self.state = "RUNNING"
        
        # Initialize segment-based control state (v2 profile format)
        if getattr(config, 'use_rate_based_control', False) and hasattr(profile, 'segments'):
            self.current_segment_index = 0
            self.segment_phase = 'ramp'
            self.segment_start_time = datetime.datetime.now()
            try:
                self.segment_start_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
            except (AttributeError, TypeError):
                self.segment_start_temp = profile.start_temp
            self.hold_start_time = None
            log.info("Using rate-based control with %d segments" % len(profile.segments))
        
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
        """Update target temperature - uses segment-based or legacy mode"""
        if getattr(config, 'use_rate_based_control', False) and hasattr(self.profile, 'segments'):
            self.target = self.calculate_rate_based_target()
            self.target_heat_rate = self.profile.get_rate_for_segment(self.current_segment_index)
        else:
            self.target = self.profile.get_target_temperature(self.runtime)
    
    # =========================================================================
    # Segment-Based Control Methods (v2 profile format)
    # =========================================================================
    
    def update_segment_progress(self):
        """
        Update which segment we're in based on actual temperature.
        Progress is temperature-based, not time-based.
        """
        if not hasattr(self.profile, 'segments') or not self.profile.segments:
            return
        
        temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        segment = self.profile.segments[self.current_segment_index]
        tolerance = getattr(config, 'segment_complete_tolerance', 5)
        
        if self.segment_phase == 'ramp':
            # Check if we've reached target temperature
            reached_target = False
            if isinstance(segment.rate, (int, float)):
                if segment.rate > 0:  # Heating
                    reached_target = temp >= segment.target - tolerance
                elif segment.rate < 0:  # Cooling
                    reached_target = temp <= segment.target + tolerance
                elif segment.rate == 0:  # Pure hold
                    reached_target = True
            elif segment.rate == "max":
                reached_target = temp >= segment.target - tolerance
            elif segment.rate == "cool":
                reached_target = temp <= segment.target + tolerance
            
            if reached_target:
                if segment.hold > 0:
                    # Transition to hold phase
                    self.segment_phase = 'hold'
                    self.hold_start_time = datetime.datetime.now()
                    log.info("Segment %d: reached target %.1f, starting %.1f min hold" % 
                             (self.current_segment_index, segment.target, segment.hold/60))
                else:
                    # Move to next segment
                    self._advance_segment()
        
        elif self.segment_phase == 'hold':
            # Check if hold time has elapsed
            if self.hold_start_time:
                hold_elapsed = (datetime.datetime.now() - self.hold_start_time).total_seconds()
                if hold_elapsed >= segment.hold:
                    self._advance_segment()
    
    def _advance_segment(self):
        """Move to the next segment"""
        self.current_segment_index += 1
        if self.current_segment_index >= len(self.profile.segments):
            log.info("All segments complete")
            self.save_firing_log(status="completed")
            self.start_cooling()
            self.state = "IDLE"
        else:
            self.segment_phase = 'ramp'
            self.segment_start_time = datetime.datetime.now()
            self.segment_start_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
            next_seg = self.profile.segments[self.current_segment_index]
            log.info("Starting segment %d: rate=%s, target=%.1f" % 
                     (self.current_segment_index, next_seg.rate, next_seg.target))
    
    def calculate_rate_based_target(self):
        """
        Calculate target temperature based on elapsed time and desired rate.
        
        The target is primarily constrained by: segment_start_temp + (rate * elapsed_hours)
        This ensures the kiln follows the specified rate, not its maximum capability.
        
        A small lead is added for PID responsiveness, but the rate-based ceiling
        is the primary constraint.
        
        Key constraints:
        1. Rate-based ceiling: start_temp + (rate * elapsed_hours)
        2. Lead for PID: rate * lookahead_seconds / 3600 (capped)
        3. Target = ceiling + lead, clamped to segment target
        """
        if not hasattr(self.profile, 'segments') or not self.profile.segments:
            return self.profile.get_target_temperature(self.runtime)
        
        if self.current_segment_index >= len(self.profile.segments):
            return 0
        
        if self.segment_phase == 'hold':
            return self.profile.segments[self.current_segment_index].target
        
        segment = self.profile.segments[self.current_segment_index]
        
        if segment.rate in ("max", "cool"):
            # For max/cool, target is the segment target
            return segment.target
        
        if segment.rate == 0:
            # Pure hold segment
            return segment.target
        
        # Get current actual temperature
        try:
            actual_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        except (AttributeError, TypeError):
            actual_temp = self.segment_start_temp if self.segment_start_temp else 0
        
        # Calculate elapsed time since segment start
        if self.segment_start_time:
            elapsed_seconds = (datetime.datetime.now() - self.segment_start_time).total_seconds()
        else:
            elapsed_seconds = 0
        elapsed_hours = elapsed_seconds / 3600
        
        # Calculate the rate-based ceiling: the maximum temperature we should have reached
        # given the elapsed time and the desired rate from segment start
        start_temp = self.segment_start_temp if self.segment_start_temp else actual_temp
        rate_based_ceiling = start_temp + (segment.rate * elapsed_hours)
        
        # Calculate lead for PID responsiveness
        lookahead_seconds = getattr(config, 'rate_lookahead_seconds', 60)  # Default 60 seconds
        effective_lookahead = min(elapsed_seconds, lookahead_seconds)
        effective_lookahead_hours = effective_lookahead / 3600
        raw_lead = segment.rate * effective_lookahead_hours  # degrees of lead
        
        # Cap the lead to prevent runaway with extreme rates
        max_divergence = getattr(config, 'max_target_divergence', 50)  # Default 50 degrees
        if abs(raw_lead) > max_divergence:
            lead = max_divergence if raw_lead > 0 else -max_divergence
        else:
            lead = raw_lead
        
        # Target is rate-based ceiling + small lead, but never exceed segment target
        # The lead helps PID be responsive, but the ceiling enforces the rate
        target_before_clamp = rate_based_ceiling + lead
        
        # Clamp to segment target
        if segment.rate > 0:  # Heating
            return min(target_before_clamp, segment.target)
        else:  # Cooling
            return max(target_before_clamp, segment.target)
    
    def check_rate_deviation(self):
        """
        Monitor actual heat rate vs target rate and log warnings if deviation is excessive.
        Replaces the old kiln_must_catch_up() behavior with logging-based feedback.
        """
        if not getattr(config, 'use_rate_based_control', False):
            return
        
        if self.segment_phase != 'ramp':
            return  # Only check during ramp phase
        
        if not hasattr(self.profile, 'segments') or self.current_segment_index >= len(self.profile.segments):
            return
        
        segment = self.profile.segments[self.current_segment_index]
        
        # Skip check for special rates
        if not isinstance(segment.rate, (int, float)) or segment.rate == 0:
            return
        
        target_rate = abs(segment.rate)
        actual_rate = abs(self.heat_rate) if self.heat_rate else 0
        deviation = abs(target_rate - actual_rate)
        
        warning_threshold = getattr(config, 'rate_deviation_warning', 50)
        if deviation > warning_threshold:
            if actual_rate < target_rate:
                log.warning(
                    "Kiln heating slower than target: actual %.1f°/hr vs target %.1f°/hr "
                    "(deviation: %.1f°/hr). Kiln may not reach temperature in expected time." %
                    (actual_rate, target_rate, deviation)
                )
            else:
                log.info(
                    "Kiln heating faster than target: actual %.1f°/hr vs target %.1f°/hr" %
                    (actual_rate, target_rate)
                )
    
    def update_schedule_progress(self):
        """
        Calculate progress based on temperature achieved and time elapsed.
        Uses time-weighted progress within segments for accurate UX.
        """
        if not hasattr(self.profile, 'segments') or not self.profile.segments:
            self.schedule_progress = 0
            return
        
        total_segments = len(self.profile.segments)
        completed_segments = self.current_segment_index
        
        # Base progress from completed segments
        base_progress = (completed_segments / total_segments) * 100
        
        # Add partial progress within current segment
        if self.current_segment_index < total_segments:
            segment = self.profile.segments[self.current_segment_index]
            current_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
            
            # Calculate estimated times for ramp and hold phases
            ramp_time = 0
            if self.segment_start_temp is not None:
                temp_range = abs(segment.target - self.segment_start_temp)
            else:
                prev_temp = self.profile.start_temp if self.current_segment_index == 0 else self.profile.segments[self.current_segment_index - 1].target
                temp_range = abs(segment.target - prev_temp)
            
            if isinstance(segment.rate, (int, float)) and segment.rate != 0:
                ramp_time = (temp_range / abs(segment.rate)) * 3600
            elif segment.rate == "max":
                max_rate = getattr(config, 'estimated_max_heating_rate', 500)
                ramp_time = (temp_range / max_rate) * 3600
            elif segment.rate == "cool":
                cool_rate = getattr(config, 'estimated_natural_cooling_rate', 100)
                ramp_time = (temp_range / cool_rate) * 3600
            
            hold_time = segment.hold
            total_segment_time = ramp_time + hold_time
            
            # Calculate weights based on actual time proportions
            ramp_weight = ramp_time / total_segment_time if total_segment_time > 0 else 1.0
            hold_weight = hold_time / total_segment_time if total_segment_time > 0 else 0.0
            
            if self.segment_phase == 'ramp':
                if temp_range > 0:
                    start_temp = self.segment_start_temp if self.segment_start_temp else (
                        self.profile.start_temp if self.current_segment_index == 0 
                        else self.profile.segments[self.current_segment_index - 1].target
                    )
                    temp_progress = abs(current_temp - start_temp) / temp_range
                    temp_progress = min(1.0, max(0.0, temp_progress))
                else:
                    temp_progress = 1.0
                segment_progress = temp_progress * ramp_weight
            else:
                # Ramp complete, now in hold phase
                if self.hold_start_time and segment.hold > 0:
                    hold_elapsed = (datetime.datetime.now() - self.hold_start_time).total_seconds()
                    hold_progress = hold_elapsed / segment.hold
                    hold_progress = min(1.0, max(0.0, hold_progress))
                else:
                    hold_progress = 1.0
                segment_progress = ramp_weight + (hold_progress * hold_weight)
            
            base_progress += (segment_progress / total_segments) * 100
        
        self.schedule_progress = min(100, base_progress)
    
    def estimate_remaining_time(self):
        """Estimate remaining time based on rates"""
        if not self.profile or not hasattr(self.profile, 'segments'):
            return 0
        
        remaining = 0
        current_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        
        # Time remaining in current segment
        if self.current_segment_index < len(self.profile.segments):
            segment = self.profile.segments[self.current_segment_index]
            
            if self.segment_phase == 'ramp':
                temp_remaining = abs(segment.target - current_temp)
                if isinstance(segment.rate, (int, float)) and segment.rate != 0:
                    remaining += (temp_remaining / abs(segment.rate)) * 3600
                elif segment.rate == "max":
                    max_rate = getattr(config, 'estimated_max_heating_rate', 500)
                    remaining += (temp_remaining / max_rate) * 3600
                elif segment.rate == "cool":
                    cool_rate = getattr(config, 'estimated_natural_cooling_rate', 100)
                    remaining += (temp_remaining / cool_rate) * 3600
                remaining += segment.hold
            elif self.segment_phase == 'hold':
                if self.hold_start_time:
                    hold_elapsed = (datetime.datetime.now() - self.hold_start_time).total_seconds()
                    remaining += max(0, segment.hold - hold_elapsed)
        
        # Add remaining segments
        prev_target = current_temp
        for i in range(self.current_segment_index + 1, len(self.profile.segments)):
            segment = self.profile.segments[i]
            temp_diff = abs(segment.target - prev_target)
            if isinstance(segment.rate, (int, float)) and segment.rate != 0:
                remaining += (temp_diff / abs(segment.rate)) * 3600
            elif segment.rate == "max":
                max_rate = getattr(config, 'estimated_max_heating_rate', 500)
                remaining += (temp_diff / max_rate) * 3600
            elif segment.rate == "cool":
                cool_rate = getattr(config, 'estimated_natural_cooling_rate', 100)
                remaining += (temp_diff / cool_rate) * 3600
            remaining += segment.hold
            prev_target = segment.target
        
        return remaining
    
    def reset_if_schedule_ended_v2(self):
        """Check if all segments are complete (v2 profile format)"""
        if not getattr(config, 'use_rate_based_control', False):
            return
        
        if not hasattr(self.profile, 'segments'):
            return
        
        # Check if we've completed all segments
        if self.current_segment_index >= len(self.profile.segments):
            if self.state == "RUNNING":
                log.info("All segments complete, shutting down")
                log.info("total cost = %s%.2f" % (config.currency_type, self.cost))
                self.save_firing_log(status="completed")
                self.start_cooling()
                self.state = "IDLE"
                self.save_automatic_restart_state()

    def reset_if_emergency(self):
        '''reset if the temperature is way TOO HOT, or other critical errors detected'''
        try:
            temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        except (AttributeError, TypeError):
            # Can't verify temperature safety without a reading
            return

        if temp >= config.emergency_shutoff_temp:
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
                return

        # Advanced Safety Checks (Stall & Runaway)
        # Only check these when actively running
        if self.state != "RUNNING":
            self.stall_start_time = None
            self.runaway_start_time = None
            return

        now = time.time()
        
        # Get effective PID duty cycle
        if hasattr(self, 'pid') and hasattr(self.pid, 'pidstats') and 'out' in self.pid.pidstats:
            duty_cycle = self.pid.pidstats['out']
        else:
            duty_cycle = 1.0 if self.heat else 0.0

        # --- Stall Detection ---
        # If heater is running hard (>95%) but temp isn't rising
        if duty_cycle > 0.95:
            if self.stall_start_time is None:
                self.stall_start_time = now
                self.stall_start_temp = temp
            elif (now - self.stall_start_time) > getattr(config, 'stall_detect_time', 1800):
                # Check temperature rise over the duration
                temp_rise = temp - self.stall_start_temp
                if temp_rise < getattr(config, 'stall_min_temp_rise', 2):
                    log.error("EMERGENCY: Kiln STALL detected. Heater >95% for %.1f min with only %.1f deg rise." % 
                              ((now - self.stall_start_time)/60, temp_rise))
                    if self.profile:
                        self.save_firing_log(status="stalled")
                    self.reset()
                    self.save_automatic_restart_state()
                    return
        else:
            self.stall_start_time = None

        # --- Runaway / Stuck Relay Detection ---
        # If heater is commanded OFF (<5%) but temp is still rising significantly
        if duty_cycle < 0.05:
            if self.runaway_start_time is None:
                self.runaway_start_time = now
                self.runaway_start_temp = temp
            elif (now - self.runaway_start_time) > getattr(config, 'runaway_detect_time', 300):
                # Check temperature rise
                temp_rise = temp - self.runaway_start_temp
                if temp_rise > getattr(config, 'runaway_min_temp_rise', 10):
                    log.error("EMERGENCY: RUNAWAY heating detected. Heater <5% for %.1f min but temp rose %.1f deg." % 
                              ((now - self.runaway_start_time)/60, temp_rise))
                    if self.profile:
                        self.save_firing_log(status="runaway")
                    self.reset()
                    self.save_automatic_restart_state()
                    return
        else:
            self.runaway_start_time = None

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
        # Calculate cost based on actual energy delivered (PID output), not just on/off state
        if hasattr(self, 'pid') and hasattr(self.pid, 'pidstats') and 'out' in self.pid.pidstats:
            duty_cycle = self.pid.pidstats['out'] # 0.0 to 1.0
        else:
            # Fallback for when PID stats aren't available yet or simulation quirks
            duty_cycle = 1.0 if self.heat else 0.0

        if duty_cycle > 0:
            cost = (config.kwh_rate * config.kw_elements * duty_cycle) * (self.time_step/3600)
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

        # Use wall clock time for heat rate calculation when using rate-based control
        time_for_heat_rate = self.actual_elapsed_time if getattr(config, 'use_rate_based_control', False) else self.runtime
        self.set_heat_rate(time_for_heat_rate, temp)

        state = {
            'cost': self.cost,
            'runtime': self.runtime,
            'actual_elapsed_time': self.actual_elapsed_time,  # Wall clock time (no seek offset)
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
            'simulate': config.simulate,
        }
        
        # Add segment-based fields when using v2 control
        if getattr(config, 'use_rate_based_control', False) and hasattr(self, 'profile') and self.profile and hasattr(self.profile, 'segments'):
            state['target_heat_rate'] = self.target_heat_rate
            state['progress'] = self.schedule_progress
            state['current_segment'] = self.current_segment_index
            state['segment_phase'] = self.segment_phase
            state['eta_seconds'] = self.estimate_remaining_time()
            state['total_segments'] = len(self.profile.segments)
        
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
            
        # Disk Protection: Throttle saves to prevent SD card wear
        now = time.time()
        # Always save if enough time has passed
        # Note: Critical state changes (like abort/complete) should call save_state() directly
        if (now - self.last_state_save) < getattr(config, 'state_save_interval', 60):
            return False

        self.save_state()
        self.last_state_save = now
        return True
    
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
        
        filename = "%s.json" % (d["profile"])
        profile_path = os.path.abspath(os.path.join(os.path.dirname( __file__ ), '..', 'storage','profiles',filename))

        with open(profile_path) as infile:
            profile_json = json.dumps(json.load(infile))
        profile = Profile(profile_json)
        
        # Check if this is v2 segment-based state
        if getattr(config, 'use_rate_based_control', False) and 'current_segment' in d and hasattr(profile, 'segments'):
            # V2 segment-based restart
            log.info("Automatic restart (v2): profile=%s, segment=%d, phase=%s" % 
                     (d["profile"], d.get("current_segment", 0), d.get("segment_phase", "ramp")))
            
            self.reset()
            self.profile = profile
            self.totaltime = profile.get_duration()
            self.start_time = datetime.datetime.now()
            
            # Restore segment-based state
            self.current_segment_index = d.get("current_segment", 0)
            self.segment_phase = d.get("segment_phase", "ramp")
            self.segment_start_time = datetime.datetime.now()
            
            # Get current temperature for segment start temp
            try:
                self.segment_start_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
            except (AttributeError, TypeError):
                self.segment_start_temp = profile.start_temp
            
            # If resuming a hold phase, adjust hold start time to account for elapsed time
            if self.segment_phase == 'hold':
                # Estimate how much hold time has passed based on eta_seconds difference
                # For simplicity, start the hold from now (conservative approach)
                self.hold_start_time = datetime.datetime.now()
                log.info("Resuming hold phase - hold timer restarted")
            
            self.cost = d.get("cost", 0)
            self.state = "RUNNING"
            
            log.info("Automatic restart: resuming segment %d (%s phase)" % 
                     (self.current_segment_index, self.segment_phase))
        else:
            # Legacy v1 restart
            startat = d["runtime"]/60
            log.info("Automatic restart (v1): profile=%s at minute=%d" % (profile_path, startat))
            self.run_profile(profile, startat=startat, allow_seek=False)
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
                # Track wall-clock time since actual start (no seek offset)
                if self.wall_clock_start_time:
                    self.actual_elapsed_time = (datetime.datetime.now() - self.wall_clock_start_time).total_seconds()
                else:
                    self.actual_elapsed_time = 0
                
                self.update_cost()
                self.track_divergence()
                self.save_automatic_restart_state()
                
                # Use segment-based or legacy control based on config
                if getattr(config, 'use_rate_based_control', False) and hasattr(self.profile, 'segments'):
                    # Segment-based control (v2)
                    self.update_segment_progress()
                    self.update_target_temp()
                    self.check_rate_deviation()
                    self.update_schedule_progress()
                    self.reset_if_schedule_ended_v2()
                else:
                    # Legacy time-based control (v1)
                    self.kiln_must_catch_up()
                    self.update_runtime()
                    self.update_target_temp()
                    self.reset_if_schedule_ended()
                
                self.heat_then_cool()
                self.reset_if_emergency()

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

        # set temps: oven starts at initial temp, heating element at environment temp
        # Use sim_initial_temp for oven, sim_t_env for heating element (allows testing pre-heated kiln)
        self.t = getattr(config, 'sim_initial_temp', config.sim_t_env)  # deg C or F temp of oven
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
        # Update actual_elapsed_time from wall clock start (no seek offset), with speedup
        if self.wall_clock_start_time:
            self.actual_elapsed_time = (datetime.datetime.now() - self.wall_clock_start_time).total_seconds() * self.speedup_factor
        else:
            self.actual_elapsed_time = 0

    def update_target_temp(self):
        """Update target temperature - uses segment-based or legacy mode"""
        # SimulatedOven MUST call update_runtime() to keep self.runtime updated
        # for heat_then_cool() which uses self.runtime for now_simulator
        self.update_runtime()
        
        if getattr(config, 'use_rate_based_control', False) and hasattr(self.profile, 'segments'):
            self.target = self.calculate_rate_based_target()
            self.target_heat_rate = self.profile.get_rate_for_segment(self.current_segment_index)
        else:
            self.target = self.profile.get_target_temperature(self.runtime)
    
    def calculate_rate_based_target(self):
        """
        SimulatedOven override: Uses same rate-based logic as parent but with speedup awareness.
        Target is based on elapsed time and desired rate, with lead for PID responsiveness.
        
        Key constraint: target cannot exceed segment_start_temp + (rate * elapsed_hours)
        This ensures the kiln follows the specified rate, not its maximum heating capability.
        """
        if not hasattr(self.profile, 'segments') or not self.profile.segments:
            return self.profile.get_target_temperature(self.runtime)
        
        if self.current_segment_index >= len(self.profile.segments):
            return 0
        
        if self.segment_phase == 'hold':
            return self.profile.segments[self.current_segment_index].target
        
        segment = self.profile.segments[self.current_segment_index]
        
        if segment.rate in ("max", "cool"):
            return segment.target
        
        if segment.rate == 0:
            return segment.target
        
        # Get current actual temperature
        try:
            actual_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        except (AttributeError, TypeError):
            actual_temp = self.segment_start_temp if self.segment_start_temp else 0
        
        # Calculate elapsed time since segment start (with speedup for simulation)
        if self.segment_start_time:
            elapsed_seconds = (datetime.datetime.now() - self.segment_start_time).total_seconds() * self.speedup_factor
        else:
            elapsed_seconds = 0
        elapsed_hours = elapsed_seconds / 3600
        
        # Calculate the rate-based ceiling: the maximum temperature we should have reached
        # given the elapsed time and the desired rate from segment start
        start_temp = self.segment_start_temp if self.segment_start_temp else actual_temp
        rate_based_ceiling = start_temp + (segment.rate * elapsed_hours)
        
        # Calculate lead for PID responsiveness
        lookahead_seconds = getattr(config, 'rate_lookahead_seconds', 60) * self.speedup_factor
        effective_lookahead = min(elapsed_seconds, lookahead_seconds)
        effective_lookahead_hours = effective_lookahead / 3600
        raw_lead = segment.rate * effective_lookahead_hours
        
        # Cap the lead to prevent runaway with extreme rates
        max_divergence = getattr(config, 'max_target_divergence', 50)
        if abs(raw_lead) > max_divergence:
            lead = max_divergence if raw_lead > 0 else -max_divergence
        else:
            lead = raw_lead
        
        # Target is rate-based ceiling + small lead, but never exceed segment target
        # The lead helps PID be responsive, but the ceiling enforces the rate
        target_before_clamp = rate_based_ceiling + lead
        
        if segment.rate > 0:
            return min(target_before_clamp, segment.target)
        else:
            return max(target_before_clamp, segment.target)
    
    def update_segment_progress(self):
        """
        SimulatedOven override: Apply speedup_factor to hold phase timing.
        """
        if not hasattr(self.profile, 'segments') or not self.profile.segments:
            return
        
        temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        segment = self.profile.segments[self.current_segment_index]
        tolerance = getattr(config, 'segment_complete_tolerance', 5)
        
        if self.segment_phase == 'ramp':
            reached_target = False
            if isinstance(segment.rate, (int, float)):
                if segment.rate > 0:
                    reached_target = temp >= segment.target - tolerance
                elif segment.rate < 0:
                    reached_target = temp <= segment.target + tolerance
                elif segment.rate == 0:
                    reached_target = True
            elif segment.rate == "max":
                reached_target = temp >= segment.target - tolerance
            elif segment.rate == "cool":
                reached_target = temp <= segment.target + tolerance
            
            if reached_target:
                if segment.hold > 0:
                    self.segment_phase = 'hold'
                    self.hold_start_time = datetime.datetime.now()
                    log.info("Segment %d: reached target %.1f, starting %.1f min hold" % 
                             (self.current_segment_index, segment.target, segment.hold/60))
                else:
                    self._advance_segment()
        
        elif self.segment_phase == 'hold':
            if self.hold_start_time:
                # Apply speedup_factor to hold elapsed time
                hold_elapsed = (datetime.datetime.now() - self.hold_start_time).total_seconds() * self.speedup_factor
                if hold_elapsed >= segment.hold:
                    self._advance_segment()

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

        # During cooling segments: only heat if kiln is cooling too fast (temp below target)
        # If kiln is at or above target, don't heat - let it cool naturally
        cooling_override = False
        current_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        if getattr(self, 'target_heat_rate', 0) and isinstance(self.target_heat_rate, (int, float)):
            if self.target_heat_rate < 0 and current_temp >= self.target:
                # Kiln is at or above target during cooling - no heat needed
                pid = 0
                self.pid.iterm = 0  # Reset integral to prevent buildup
                self.pid.pidstats['out'] = 0  # Update pidstats so frontend shows heat off
                cooling_override = True

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

        # During cooling segments: only heat if kiln is cooling too fast (temp below target)
        # If kiln is at or above target, don't heat - let it cool naturally
        cooling_override = False
        current_temp = self.board.temp_sensor.temperature() + config.thermocouple_offset
        if getattr(self, 'target_heat_rate', 0) and isinstance(self.target_heat_rate, (int, float)):
            if self.target_heat_rate < 0 and current_temp >= self.target:
                # Kiln is at or above target during cooling - no heat needed
                pid = 0
                self.pid.iterm = 0  # Reset integral to prevent buildup
                self.pid.pidstats['out'] = 0  # Update pidstats so frontend shows heat off
                cooling_override = True

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

class Segment:
    """Represents a single firing segment in a rate-based profile (v2 format)"""
    
    def __init__(self, rate, target, hold=0):
        """
        Initialize a firing segment.
        
        Args:
            rate: Heat rate in degrees/hour. Can be:
                  - positive number: heating rate
                  - negative number: cooling rate  
                  - 0: hold at current temperature
                  - "max": heat as fast as possible
                  - "cool": cool naturally (no power)
            target: Target temperature for this segment
            hold: Hold time in minutes (stored internally as seconds)
        """
        self.rate = rate
        self.target = target
        self.hold = hold * 60  # Convert minutes to seconds
    
    def is_ramp(self):
        """Returns True if this segment has a ramp phase (non-zero numeric rate)"""
        return isinstance(self.rate, (int, float)) and self.rate != 0
    
    def is_pure_hold(self):
        """Returns True if this is a hold-only segment (rate=0)"""
        return self.rate == 0
    
    def has_hold_phase(self):
        """Returns True if this segment includes any hold time"""
        return self.hold > 0
    
    def is_max_power(self):
        """Returns True if this segment uses maximum heating rate"""
        return self.rate == "max"
    
    def is_natural_cool(self):
        """Returns True if this segment uses natural cooling"""
        return self.rate == "cool"
    
    def validate(self, previous_target=None):
        """
        Validate segment configuration.
        
        Args:
            previous_target: Temperature at the start of this segment
            
        Raises:
            ValueError: If segment configuration is invalid
        """
        if previous_target is not None and isinstance(self.rate, (int, float)):
            if self.rate < 0 and self.target > previous_target:
                raise ValueError(
                    f"Negative rate ({self.rate}) with increasing target "
                    f"({previous_target} -> {self.target})"
                )
            if self.rate > 0 and self.target < previous_target:
                raise ValueError(
                    f"Positive rate ({self.rate}) with decreasing target "
                    f"({previous_target} -> {self.target})"
                )
    
    def __repr__(self):
        return f"Segment(rate={self.rate}, target={self.target}, hold={self.hold/60}min)"


class Profile():
    def __init__(self, json_data):
        obj = json.loads(json_data)
        self.name = obj["name"]
        self.version = obj.get("version", 1)
        
        if self.version == 1:
            # Legacy format - load data and convert to segments
            self.data = sorted(obj["data"])
            self._load_legacy(obj)
        else:
            # V2 format - load segments directly
            self._load_v2(obj)
            # Generate legacy data for graph compatibility
            self.data = self.to_legacy_format()
    
    def _load_legacy(self, obj):
        """Convert legacy time-based format to segments"""
        self.start_temp = obj["data"][0][1] if obj["data"] else 0
        self.temp_units = obj.get("temp_units", "f")
        self.segments = []
        
        for i in range(1, len(obj["data"])):
            prev_time, prev_temp = obj["data"][i-1]
            curr_time, curr_temp = obj["data"][i]
            
            time_diff = curr_time - prev_time  # seconds
            temp_diff = curr_temp - prev_temp  # degrees
            
            if time_diff > 0:
                if temp_diff != 0:
                    # Calculate rate in degrees/hour
                    rate = (temp_diff / time_diff) * 3600
                    self.segments.append(Segment(rate, curr_temp, hold=0))
                else:
                    # This is a hold - merge with previous segment if possible
                    hold_minutes = time_diff / 60
                    if self.segments and self.segments[-1].target == curr_temp:
                        # Add hold time to the previous segment (in seconds)
                        self.segments[-1].hold += hold_minutes * 60
                    else:
                        # Create standalone hold segment only if no previous segment
                        self.segments.append(Segment(0, curr_temp, hold=hold_minutes))
    
    def _load_v2(self, obj):
        """Load v2 rate-based format with temperature unit conversion"""
        self.start_temp = obj.get("start_temp", 0)
        self.temp_units = obj.get("temp_units", "f")
        self.segments = []
        
        # Check if conversion needed (profile in C, system in F or vice versa)
        needs_c_to_f = self.temp_units == "c" and config.temp_scale.lower() == "f"
        needs_f_to_c = self.temp_units == "f" and config.temp_scale.lower() == "c"
        
        if needs_c_to_f:
            self.start_temp = (self.start_temp * 9 / 5) + 32
        elif needs_f_to_c:
            self.start_temp = (self.start_temp - 32) * 5 / 9
        
        for seg in obj.get("segments", []):
            target = seg["target"]
            rate = seg["rate"]
            
            # Convert temperatures and rates if needed
            if needs_c_to_f:
                target = (target * 9 / 5) + 32
                if isinstance(rate, (int, float)):
                    rate = rate * 9 / 5  # Rate conversion: °C/hr to °F/hr
            elif needs_f_to_c:
                target = (target - 32) * 5 / 9
                if isinstance(rate, (int, float)):
                    rate = rate * 5 / 9  # Rate conversion: °F/hr to °C/hr
            
            segment = Segment(
                rate=rate,
                target=target,
                hold=seg.get("hold", 0)
            )
            
            # Validate segment rate direction vs temperature change
            previous_target = self.segments[-1].target if self.segments else self.start_temp
            segment.validate(previous_target)
            
            self.segments.append(segment)
    
    def to_legacy_format(self, start_temp=None):
        """
        Convert segments back to legacy format for graph compatibility.
        
        Returns:
            list: Array of [time_seconds, temperature] tuples
        """
        if start_temp is None:
            start_temp = self.start_temp
        
        data = [[0, start_temp]]
        current_time = 0
        current_temp = start_temp
        
        for segment in self.segments:
            if isinstance(segment.rate, str):
                # Estimate time for special rates
                if segment.rate == "max":
                    temp_diff = segment.target - current_temp
                    # Use estimated max heating rate from config
                    max_rate = getattr(config, 'estimated_max_heating_rate', 500)
                    time_seconds = abs(temp_diff) / max_rate * 3600
                else:  # "cool"
                    temp_diff = current_temp - segment.target
                    # Use estimated natural cooling rate from config
                    cool_rate = getattr(config, 'estimated_natural_cooling_rate', 100)
                    time_seconds = abs(temp_diff) / cool_rate * 3600
                # Add ramp point for special rates
                current_time += time_seconds
                current_temp = segment.target
                data.append([current_time, current_temp])
            elif segment.rate != 0:
                # Normal ramp segment
                temp_diff = segment.target - current_temp
                time_hours = abs(temp_diff) / abs(segment.rate)
                time_seconds = time_hours * 3600
                current_time += time_seconds
                current_temp = segment.target
                data.append([current_time, current_temp])
            # For rate=0 (pure hold), don't add a ramp point - just add the hold below
            
            # Add hold point if needed (applies to all segment types)
            if segment.hold > 0:
                current_time += segment.hold
                data.append([current_time, current_temp])
        
        return data
    
    def estimate_duration(self, start_temp=None):
        """
        Estimate total duration based on rates.
        This is an estimate since actual time depends on kiln performance.
        
        Returns:
            float: Estimated duration in seconds
        """
        if start_temp is None:
            start_temp = self.start_temp
        
        total_seconds = 0
        current_temp = start_temp
        
        for segment in self.segments:
            if isinstance(segment.rate, str):
                # Can't estimate "max" or "cool" accurately
                if segment.rate == "max":
                    max_rate = getattr(config, 'estimated_max_heating_rate', 500)
                    temp_diff = abs(segment.target - current_temp)
                    total_seconds += (temp_diff / max_rate) * 3600
                elif segment.rate == "cool":
                    cool_rate = getattr(config, 'estimated_natural_cooling_rate', 100)
                    temp_diff = abs(current_temp - segment.target)
                    total_seconds += (temp_diff / cool_rate) * 3600
            elif segment.rate != 0:
                temp_diff = abs(segment.target - current_temp)
                time_hours = temp_diff / abs(segment.rate)
                total_seconds += time_hours * 3600
            
            total_seconds += segment.hold  # Add hold time
            current_temp = segment.target
        
        return total_seconds
    
    def get_segment_for_temperature(self, current_temp, segment_index=0):
        """
        Determine which segment we should be in based on current temperature.
        
        Args:
            current_temp: Current kiln temperature
            segment_index: Current segment index
            
        Returns:
            tuple: (segment_index, segment, phase) where phase is 'ramp', 'hold', or 'complete'
        """
        if segment_index >= len(self.segments):
            return (len(self.segments) - 1, self.segments[-1], 'complete')
        
        segment = self.segments[segment_index]
        
        # Check if we've reached the target for this segment
        tolerance = getattr(config, 'segment_complete_tolerance', 5)
        
        if segment.rate == 0:  # Explicit hold segment
            return (segment_index, segment, 'hold')
        elif segment.rate == "max":
            if current_temp >= segment.target - tolerance:
                return (segment_index, segment, 'hold')
        elif segment.rate == "cool":
            if current_temp <= segment.target + tolerance:
                return (segment_index, segment, 'hold')
        elif isinstance(segment.rate, (int, float)):
            if segment.rate > 0:  # Heating
                if current_temp >= segment.target - tolerance:
                    return (segment_index, segment, 'hold')
            elif segment.rate < 0:  # Cooling
                if current_temp <= segment.target + tolerance:
                    return (segment_index, segment, 'hold')
        
        return (segment_index, segment, 'ramp')
    
    def get_rate_for_segment(self, segment_index):
        """Get heat rate for current segment"""
        if segment_index >= len(self.segments):
            return 0
        return self.segments[segment_index].rate
    
    def get_hold_duration(self, segment_index):
        """Get hold duration in seconds for segment"""
        if segment_index >= len(self.segments):
            return 0
        return self.segments[segment_index].hold

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
            # Reset integral term when significantly above target (cooling mode)
            # This prevents the accumulated heating integral from causing unwanted heat output
            if self.iterm > 0:
                log.info("Resetting positive integral term during cooling: %.2f -> 0" % self.iterm)
                self.iterm = 0
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
