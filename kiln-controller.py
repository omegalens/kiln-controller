#!/usr/bin/env python

import time
import os
import sys
import logging
import json

import bottle
import gevent
import geventwebsocket
#from bottle import post, get
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler
from geventwebsocket import WebSocketError

# try/except removed here on purpose so folks can see why things break
import config

logging.basicConfig(level=config.log_level, format=config.log_format)
log = logging.getLogger("kiln-controller")
log.info("Starting kiln controller")

script_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, script_dir + '/lib/')
profile_path = config.kiln_profiles_directory

from oven import SimulatedOven, RealOven, Profile
from ovenWatcher import OvenWatcher

app = bottle.Bottle()

if config.simulate == True:
    log.info("this is a simulation")
    oven = SimulatedOven()
else:
    log.info("this is a real kiln")
    oven = RealOven()
ovenWatcher = OvenWatcher(oven)
# this ovenwatcher is used in the oven class for restarts
oven.set_ovenwatcher(ovenWatcher)

@app.route('/')
def index():
    return bottle.redirect('/picoreflow/index.html')

@app.route('/state')
def state():
    return bottle.redirect('/picoreflow/state.html')

@app.get('/api/stats')
def handle_api():
    log.info("/api/stats command received")
    if hasattr(oven,'pid'):
        if hasattr(oven.pid,'pidstats'):
            return json.dumps(oven.pid.pidstats)


@app.post('/api')
def handle_api():
    log.info("/api command received")
    
    # Validate request has JSON
    if not bottle.request.json:
        return {"success": False, "error": "No JSON data provided"}
    
    # Validate cmd field exists
    if 'cmd' not in bottle.request.json:
        return {"success": False, "error": "No cmd field in request"}
    
    cmd = bottle.request.json['cmd']
    
    # Use elif for mutually exclusive commands
    if cmd == 'run':
        wanted = bottle.request.json.get('profile')
        if not wanted:
            return {"success": False, "error": "No profile specified"}
        
        log.info('api requested run of profile = %s' % wanted)
        
        # Start at a specific minute
        startat = bottle.request.json.get('startat', 0)
        
        # Shut off seek if start time has been set
        allow_seek = True
        if startat > 0:
            allow_seek = False
        
        profile = find_profile(wanted)
        if profile is None:
            return {"success": False, "error": "profile %s not found" % wanted}
        
        profile_json = json.dumps(profile)
        profile = Profile(profile_json)
        oven.run_profile(profile, startat=startat, allow_seek=allow_seek)
        ovenWatcher.record(profile)
        return {"success": True}
    
    elif cmd == 'pause':
        log.info("api pause command received")
        if oven.state == 'RUNNING':
            oven.state = 'PAUSED'
            return {"success": True}
        else:
            return {"success": False, "error": "Cannot pause, oven state is %s" % oven.state}
    
    elif cmd == 'resume':
        log.info("api resume command received")
        if oven.state == 'PAUSED':
            oven.state = 'RUNNING'
            return {"success": True}
        else:
            return {"success": False, "error": "Cannot resume, oven state is %s" % oven.state}
    
    elif cmd == 'stop':
        log.info("api stop command received")
        oven.abort_run()
        return {"success": True}
    
    elif cmd == 'memo':
        log.info("api memo command received")
        memo = bottle.request.json.get('memo', '')
        log.info("memo=%s" % memo)
        return {"success": True}
    
    elif cmd == 'stats':
        log.info("api stats command received")
        if hasattr(oven, 'pid') and hasattr(oven.pid, 'pidstats'):
            return json.dumps(oven.pid.pidstats)
        else:
            return {"success": False, "error": "No PID stats available"}
    
    else:
        return {"success": False, "error": "Unknown command: %s" % cmd}

def find_profile(wanted):
    '''
    given a wanted profile name, find it and return the parsed
    json profile object or None.
    '''
    #load all profiles from disk
    profiles = get_profiles()
    json_profiles = json.loads(profiles)

    # find the wanted profile
    for profile in json_profiles:
        if profile['name'] == wanted:
            return profile
    return None

@app.route('/picoreflow/:filename#.*#')
def send_static(filename):
    log.debug("serving %s" % filename)
    response = bottle.static_file(filename, root=os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), "public"))
    # Prevent caching of JS files to ensure fresh loads during development
    if filename.endswith('.js'):
        response.set_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        response.set_header('Pragma', 'no-cache')
        response.set_header('Expires', '0')
    return response


def get_websocket_from_request():
    env = bottle.request.environ
    wsock = env.get('wsgi.websocket')
    if not wsock:
        abort(400, 'Expected WebSocket request.')
    return wsock


@app.route('/control')
def handle_control():
    wsock = get_websocket_from_request()
    log.info("websocket (control) opened")
    while True:
        try:
            message = wsock.receive()
            if message:
                log.info("Received (control): %s" % message)
                msgdict = json.loads(message)
                if msgdict.get("cmd") == "RUN":
                    log.info("RUN command received")
                    profile_obj = msgdict.get('profile')
                    if profile_obj:
                        profile_json = json.dumps(profile_obj)
                        profile = Profile(profile_json)
                    oven.run_profile(profile)
                    ovenWatcher.record(profile)
                elif msgdict.get("cmd") == "SIMULATE":
                    log.info("SIMULATE command received")
                    #profile_obj = msgdict.get('profile')
                    #if profile_obj:
                    #    profile_json = json.dumps(profile_obj)
                    #    profile = Profile(profile_json)
                    #simulated_oven = Oven(simulate=True, time_step=0.05)
                    #simulation_watcher = OvenWatcher(simulated_oven)
                    #simulation_watcher.add_observer(wsock)
                    #simulated_oven.run_profile(profile)
                    #simulation_watcher.record(profile)
                elif msgdict.get("cmd") == "STOP":
                    log.info("Stop command received")
                    oven.abort_run()
            time.sleep(1)
        except WebSocketError as e:
            log.error(e)
            break
    log.info("websocket (control) closed")


@app.route('/storage')
def handle_storage():
    wsock = get_websocket_from_request()
    log.info("websocket (storage) opened")
    while True:
        try:
            message = wsock.receive()
            if not message:
                break
            log.debug("websocket (storage) received: %s" % message)

            try:
                msgdict = json.loads(message)
            except:
                msgdict = {}

            if message == "GET":
                log.info("GET command received")
                wsock.send(get_profiles())
            elif msgdict.get("cmd") == "DELETE":
                log.info("DELETE command received")
                profile_obj = msgdict.get('profile')
                if delete_profile(profile_obj):
                  msgdict["resp"] = "OK"
                wsock.send(json.dumps(msgdict))
                #wsock.send(get_profiles())
            elif msgdict.get("cmd") == "PUT":
                log.info("PUT command received")
                profile_obj = msgdict.get('profile')
                #force = msgdict.get('force', False)
                force = True
                if profile_obj:
                    #del msgdict["cmd"]
                    if save_profile(profile_obj, force):
                        msgdict["resp"] = "OK"
                    else:
                        msgdict["resp"] = "FAIL"
                    log.debug("websocket (storage) sent: %s" % message)

                    wsock.send(json.dumps(msgdict))
                    wsock.send(get_profiles())
            time.sleep(1) 
        except WebSocketError:
            break
    log.info("websocket (storage) closed")


@app.route('/config')
def handle_config():
    wsock = get_websocket_from_request()
    log.info("websocket (config) opened")
    while True:
        try:
            message = wsock.receive()
            wsock.send(get_config())
        except WebSocketError:
            break
        time.sleep(1)
    log.info("websocket (config) closed")


@app.route('/status')
def handle_status():
    wsock = get_websocket_from_request()
    ovenWatcher.add_observer(wsock)
    log.info("websocket (status) opened")
    while True:
        try:
            message = wsock.receive()
            wsock.send("Your message was: %r" % message)
        except WebSocketError:
            break
        time.sleep(1)
    log.info("websocket (status) closed")


def get_profiles():
    try:
        profile_files = os.listdir(profile_path)
    except:
        profile_files = []
    profiles = []
    for filename in profile_files:
        with open(os.path.join(profile_path, filename), 'r') as f:
            profiles.append(json.load(f))
    profiles = normalize_temp_units(profiles)
    return json.dumps(profiles)


def save_profile(profile, force=False):
    """Save profile to disk in Fahrenheit (standard format)"""
    # Ensure profile has temp_units
    profile = add_temp_units(profile)
    
    # Convert to Fahrenheit for storage if needed (maintain F as standard)
    if profile['temp_units'] == "c":
        profile = convert_to_f(profile)
        profile['temp_units'] = "f"
    
    profile_json = json.dumps(profile)
    filename = profile['name']+".json"
    filepath = os.path.join(profile_path, filename)
    if not force and os.path.exists(filepath):
        log.error("Could not write, %s already exists" % filepath)
        return False
    with open(filepath, 'w+') as f:
        f.write(profile_json)
        f.close()
    log.info("Wrote %s" % filepath)
    return True

def add_temp_units(profile):
    """
    Ensures profile has temp_units field.
    
    Policy:
    - If profile already has temp_units, trust it (idempotent)
    - If profile lacks temp_units, assume it's in Fahrenheit (matches existing profiles)
    - This function only adds metadata, doesn't convert data
    """
    if "temp_units" not in profile:
        profile['temp_units'] = "f"  # Assume Fahrenheit (default storage format)
    return profile

def convert_to_c(profile):
    """Convert profile temperatures from Fahrenheit to Celsius"""
    # Only convert if not already in Celsius (idempotent)
    if profile.get("temp_units") == "c":
        return profile
    
    # Handle v2 format (segments)
    if profile.get("version", 1) >= 2 and "segments" in profile:
        if "start_temp" in profile:
            profile["start_temp"] = (profile["start_temp"] - 32) * 5 / 9
        for segment in profile.get("segments", []):
            segment["target"] = (segment["target"] - 32) * 5 / 9
            # Convert rate (degrees/hour)
            if isinstance(segment["rate"], (int, float)):
                segment["rate"] = segment["rate"] * 5 / 9
    else:
        # Handle v1 format (data points)
        newdata = []
        for (secs, temp) in profile.get("data", []):
            temp_c = (temp - 32) * 5 / 9
            newdata.append((secs, temp_c))
        profile["data"] = newdata
    
    profile["temp_units"] = "c"
    return profile

def convert_to_f(profile):
    """Convert profile temperatures from Celsius to Fahrenheit"""
    # Only convert if not already in Fahrenheit (idempotent)
    if profile.get("temp_units") == "f":
        return profile
    
    # Handle v2 format (segments)
    if profile.get("version", 1) >= 2 and "segments" in profile:
        if "start_temp" in profile:
            profile["start_temp"] = (profile["start_temp"] * 9 / 5) + 32
        for segment in profile.get("segments", []):
            segment["target"] = (segment["target"] * 9 / 5) + 32
            # Convert rate (degrees/hour)
            if isinstance(segment["rate"], (int, float)):
                segment["rate"] = segment["rate"] * 9 / 5
    else:
        # Handle v1 format (data points)
        newdata = []
        for (secs, temp) in profile.get("data", []):
            temp_f = (temp * 9 / 5) + 32
            newdata.append((secs, temp_f))
        profile["data"] = newdata
    
    profile["temp_units"] = "f"
    return profile

def normalize_temp_units(profiles):
    """
    Convert profiles to display in config.temp_scale.
    Creates deep copies - doesn't modify originals.
    """
    import copy
    normalized = []
    
    for profile in profiles:
        # Deep copy to avoid modifying the original
        display_profile = copy.deepcopy(profile)
        
        # Ensure it has temp_units (defaults to F)
        display_profile = add_temp_units(display_profile)
        
        # Convert to match config for display if needed
        if config.temp_scale == "c" and display_profile["temp_units"] == "f":
            display_profile = convert_to_c(display_profile)
        elif config.temp_scale == "f" and display_profile["temp_units"] == "c":
            display_profile = convert_to_f(display_profile)
        
        normalized.append(display_profile)
    
    return normalized

def delete_profile(profile):
    profile_json = json.dumps(profile)
    filename = profile['name']+".json"
    filepath = os.path.join(profile_path, filename)
    os.remove(filepath)
    log.info("Deleted %s" % filepath)
    return True

def get_config():
    return json.dumps({"temp_scale": config.temp_scale,
        "time_scale_slope": config.time_scale_slope,
        "time_scale_profile": config.time_scale_profile,
        "kwh_rate": config.kwh_rate,
        "kw_elements": config.kw_elements,
        "currency_type": config.currency_type})

@app.get('/api/last_firing')
def get_last_firing():
    """Return summary of the last completed firing"""
    log.info("/api/last_firing command received")
    try:
        if os.path.exists(config.last_firing_file):
            with open(config.last_firing_file, 'r') as f:
                last_firing = json.load(f)
            return json.dumps(last_firing)
        else:
            return json.dumps({"error": "No firing history available"})
    except Exception as e:
        log.error(f"Error reading last firing: {e}")
        return json.dumps({"error": "Failed to read firing history"})

@app.get('/api/firing_logs')
def get_firing_logs():
    """Return list of all firing log files"""
    log.info("/api/firing_logs command received")
    try:
        if not os.path.exists(config.firing_logs_directory):
            return json.dumps([])
        
        log_files = []
        for filename in sorted(os.listdir(config.firing_logs_directory), reverse=True):
            if filename.endswith('.json'):
                filepath = os.path.join(config.firing_logs_directory, filename)
                try:
                    with open(filepath, 'r') as f:
                        log_data = json.load(f)
                    # Return summary info only
                    log_files.append({
                        'filename': filename,
                        'profile_name': log_data.get('profile_name'),
                        'end_time': log_data.get('end_time'),
                        'duration_seconds': log_data.get('duration_seconds'),
                        'final_cost': log_data.get('final_cost'),
                        'avg_divergence': log_data.get('avg_divergence'),
                        'status': log_data.get('status')
                    })
                except Exception as e:
                    log.error(f"Error reading log file {filename}: {e}")
                    continue
        
        return json.dumps(log_files)
    except Exception as e:
        log.error(f"Error listing firing logs: {e}")
        return json.dumps({"error": "Failed to list firing logs"})    

def main():
    ip = "0.0.0.0"
    port = config.listening_port
    log.info("listening on %s:%d" % (ip, port))

    server = WSGIServer((ip, port), app,
                        handler_class=WebSocketHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
