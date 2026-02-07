#! /usr/bin/env python3
# 2021-04-02
# 2021-04-13    Fix Wrong model for Old Style revision codes
# 2021-12-20    Improve Old Style revision codes; ignore unwanted status bits
# 2022-03-25    Zero 2 W
# 2022-04-07    typo
"""
Read all GPIO
This version uses RPi.GPIO library
"""
import sys, os, time
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    print("Warning: RPi.GPIO not available. Cannot read GPIO states.")

MODES=["IN", "OUT", "ALT5", "ALT4", "ALT0", "ALT1", "ALT2", "ALT3"]
HEADER = ('3.3v', '5v', 2, '5v', 3, 'GND', 4, 14, 'GND', 15, 17, 18, 27, 'GND', 22, 23, '3.3v', 24, 10, 'GND', 9, 25, 11, 8, 'GND', 7, 0, 1, 5, 'GND', 6, 12, 13, 'GND', 19, 16, 26, 20, 'GND', 21)

# https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#new-style-revision-codes
PiModel = {
0: 'A',
1: 'B',
2: 'A+',
3: 'B+',
4: '2B',
6: 'CM1',
8: '3B',
9: 'Zero',
0xa: 'CM3',
0xc: 'ZeroW',
0xd: '3B+',
0xe: '3A+',
0x10: 'CM3+',
0x11: '4B',
0x12: 'Zero2W',
0x13: '400',
0x14: 'CM4'
}

RED   = '\033[1;31m'
GREEN = '\033[1;32m'
ORANGE = '\033[1;33m'
BLUE = '\033[1;34m'
LRED = '\033[1;91m'
YELLOW = '\033[1;93m'
RESET = '\033[0;0m'
COL = {
    '3.3v': LRED,
    '5v': RED,
    'GND': GREEN
}

TYPE = 0
rev = 0

# GPIO pin alternate function names mapping
# Common alternate functions for Raspberry Pi GPIO pins
ALT_FUNCTIONS = {
    2: 'SDA.1', 3: 'SCL.1', 4: 'GPIO.7', 14: 'TxD', 15: 'RxD',
    17: 'GPIO.0', 18: 'GPIO.1', 27: 'GPIO.2', 22: 'GPIO.3',
    23: 'GPIO.4', 24: 'GPIO.5', 10: 'MOSI', 9: 'MISO',
    11: 'SCLK', 8: 'CE0', 7: 'CE1', 0: 'SDA.0', 1: 'SCL.0',
    5: 'GPIO.21', 6: 'GPIO.22', 13: 'GPIO.23', 19: 'GPIO.24',
    26: 'GPIO.25', 12: 'GPIO.26', 16: 'GPIO.27', 20: 'GPIO.28',
    21: 'GPIO.29'
}

def pin_state(g):
    """
    Return "state" of BCM g
    Return is tuple (name, mode, value)
    """
    if not GPIO_AVAILABLE:
        return 'GPIO{}'.format(g), 'N/A', '?'
    
    # First, try to read from /sys/class/gpio (non-invasive)
    gpio_path = '/sys/class/gpio/gpio{}'.format(g)
    if os.path.exists(gpio_path):
        try:
            with open(os.path.join(gpio_path, 'direction'), 'r') as f:
                direction = f.read().strip()
            with open(os.path.join(gpio_path, 'value'), 'r') as f:
                value = int(f.read().strip())
            mode = direction.upper()
            name = 'GPIO{}'.format(g)
            return name, mode, value
        except:
            pass
    
    # If not in /sys/class/gpio, pin might be in alternate function mode
    # or not exported. Try to read it with RPi.GPIO (this will temporarily
    # configure it, but we'll try to be safe)
    try:
        GPIO.setup(g, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
        value = GPIO.input(g)
        mode = 'IN'
        name = 'GPIO{}'.format(g)
        
        # Try to detect pull configuration
        GPIO.setup(g, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        value_up = GPIO.input(g)
        GPIO.setup(g, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        value_down = GPIO.input(g)
        
        if value_up == 1 and value_down == 0:
            mode = 'IN ^'  # Pull-up
        elif value_up == 0 and value_down == 0:
            mode = 'IN v'  # Pull-down
        
        # Reset to no pull
        GPIO.setup(g, GPIO.IN, pull_up_down=GPIO.PUD_OFF)
        
        return name, mode, value
        
    except RuntimeError:
        # Pin is likely in alternate function mode (SPI, I2C, UART, etc.)
        # or in use by another process
        name = ALT_FUNCTIONS.get(g, 'GPIO{}'.format(g))
        mode = 'ALT'
        value = '?'
        return name, mode, value
    except Exception:
        # Any other error - pin might be reserved or unavailable
        name = ALT_FUNCTIONS.get(g, 'GPIO{}'.format(g))
        mode = 'N/A'
        value = '?'
        return name, mode, value

def print_gpio(pin_state):
    """
    Print listing of Raspberry pins, state & value
    Layout matching Pi 2 row Header
    """
    global TYPE, rev
    GPIOPINS = 40
    try:
        Model = 'Pi ' + PiModel[TYPE]
    except:
        Model = 'Pi ??'
    if rev < 16 :	# older models (pre PiB+)
        GPIOPINS = 26

    print('+-----+------------+------+---+{:^10}+---+------+-----------+-----+'.format(Model) )
    print('| BCM |    Name    | Mode | V |  Board   | V | Mode | Name      | BCM |')
    print('+-----+------------+------+---+----++----+---+------+-----------+-----+')

    for h in range(1, GPIOPINS, 2):
    # odd pin
        hh = HEADER[h-1]
        if(type(hh)==type(1)):
            print('|{0:4} | {1[0]:<10} | {1[1]:<4} | {1[2]} |{2:3} '.format(hh, pin_state(hh), h), end='|| ')
        else:
#             print('|        {:18}   | {:2}'.format(hh, h), end=' || ')    # non-coloured output
            print('|        {}{:18}   | {:2}{}'.format(COL[hh], hh, h, RESET), end=' || ')    # coloured output
    # even pin
        hh = HEADER[h]
        if(type(hh)==type(1)):
            print('{0:2} | {1[2]:<2}| {1[1]:<5}| {1[0]:<10}|{2:4} |'.format(h+1, pin_state(hh), hh))
        else:
#             print('{:2} |             {:9}      |'.format(h+1, hh))    # non-coloured output
            print('{}{:2} |             {:9}{}      |'.format(COL[hh], h+1, hh, RESET))    # coloured output
    print('+-----+------------+------+---+----++----+---+------+-----------+-----+')
    print('| BCM |    Name    | Mode | V |  Board   | V | Mode | Name      | BCM |')
    print('+-----+------------+------+---+{:^10}+---+------+-----------+-----+'.format(Model) )

def get_hardware_revision():
    """
    Returns the Pi's hardware revision number.
    """
    with open('/proc/cpuinfo', 'r') as f:
        for line in f.readlines():
            if 'Revision' in line:
                REV = line.split(':')[1]
                REV = REV.strip()   # Revision as string
                return int(REV, base=16)

def main():
    global TYPE, rev
    
    if not GPIO_AVAILABLE:
        print("Error: RPi.GPIO is not available.")
        print("Make sure you're running on a Raspberry Pi and RPi.GPIO is installed:")
        print("  pip install RPi.GPIO")
        sys.exit(1)
    
    # Initialize GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)  # Suppress warnings about pins already in use
    
    rev = get_hardware_revision()

    if(rev & 0x800000):   # New Style
        TYPE = (rev&0x00000FF0)>>4
    else:   # Old Style
        rev &= 0x1F
        MM = [0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 3, 6, 2, 3, 6, 2]
        TYPE = MM[rev] # Map Old Style revision to TYPE

    try:
        print_gpio(pin_state)
    finally:
        # Clean up GPIO
        GPIO.cleanup()

if __name__ == '__main__':
	main()

