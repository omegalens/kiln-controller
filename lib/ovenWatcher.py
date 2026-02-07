import threading,logging,json,time,datetime
from oven import Oven
log = logging.getLogger(__name__)

class OvenWatcher(threading.Thread):
    def __init__(self,oven):
        self.last_profile = None
        self.last_log = []
        self.started = None
        self.recording = False
        self.observers = []
        self.adjusted_profile_data = None
        threading.Thread.__init__(self)
        self.daemon = True
        self.oven = oven
        self.start()

# FIXME - need to save runs of schedules in near-real-time
# FIXME - this will enable re-start in case of power outage
# FIXME - re-start also requires safety start (pausing at the beginning
# until a temp is reached)
# FIXME - re-start requires a time setting in minutes.  if power has been
# out more than N minutes, don't restart
# FIXME - this should not be done in the Watcher, but in the Oven class

    def run(self):
        while True:
            oven_state = self.oven.get_state()
           
            # record state for any new clients that join
            if oven_state.get("state") == "RUNNING":
                self.last_log.append(oven_state)
            else:
                self.recording = False
            self.notify_all(oven_state)
            time.sleep(self.oven.time_step)

    def lastlog_subset(self,maxpts=50):
        '''send about maxpts from lastlog by skipping unwanted data'''
        totalpts = len(self.last_log)
        if (totalpts <= maxpts):
            return self.last_log
        every_nth = int(totalpts / (maxpts - 1))
        return self.last_log[::every_nth]

    def record(self, profile):
        self.last_profile = profile
        self.last_log = []
        self.started = datetime.datetime.now()
        self.recording = True
        #we just turned on, add first state for nice graph
        first_state = self.oven.get_state()
        self.last_log.append(first_state)
        
        # Compute adjusted profile curve starting from the kiln's actual temperature.
        # Stored separately so we never mutate the original profile object.
        actual_temp = first_state.get('temperature', profile.start_temp)
        if hasattr(profile, 'segments') and profile.segments:
            self.adjusted_profile_data = profile.to_legacy_format(start_temp=actual_temp)
        elif profile.data and len(profile.data) > 0:
            self.adjusted_profile_data = [[0, actual_temp]] + [list(pt) for pt in profile.data[1:]]
        else:
            self.adjusted_profile_data = profile.data
        
        # Broadcast the adjusted profile to all already-connected clients
        profile_update = {
            'type': 'profile_update',
            'data': self.adjusted_profile_data
        }
        self.notify_all(profile_update)

    def add_observer(self,observer):
        if self.last_profile:
            # During an active firing, send the adjusted profile curve;
            # otherwise send the original profile data.
            if self.recording and self.adjusted_profile_data:
                profile_data = self.adjusted_profile_data
            else:
                profile_data = self.last_profile.data
            p = {
                "name": self.last_profile.name,
                "data": profile_data,
                "type" : "profile"
            }
        else:
            p = None
        
        log_subset = self.lastlog_subset()
        backlog = {
            'type': "backlog",
            'profile': p,
            'log': log_subset,
        }
        
        backlog_json = json.dumps(backlog)
        try:
            observer.send(backlog_json)
        except:
            log.error("Could not send backlog to new observer")
        
        self.observers.append(observer)

    def notify_all(self,message):
        message_json = json.dumps(message)
        log.debug("sending to %d clients: %s"%(len(self.observers),message_json))

        for wsock in self.observers[:]:
            if wsock:
                try:
                    wsock.send(message_json)
                except Exception as e:
                    log.error("could not write to socket %s: %s" % (wsock, e))
                    self.observers.remove(wsock)
            else:
                self.observers.remove(wsock)
