from multiprocessing import connection
import threading
import time

from onedrive_fuse import common, metrics, commonfunc
from onedrive_fuse.log import logger
from onedrive_fuse.remote import api
from onedrive_fuse.threads import refresh

class Heartbeat:
    def __init__(self):
        self.started = False   
        self.stopped = False  

    def start(self):
        if self.started == False:
            logger.info('heartbeat_start')
            metrics.counts.incr('heartbeat_start')
            self.started = True
            threading.Thread(target=self.heartbeatThread, daemon=True).start()
 
    def heartbeatThread(self):   
        common.threadLocal.operation = 'heartbeat' 
        common.threadLocal.path = None          
        metrics.counts.incr('heartbeat_heartbeatThread')
        while True:
            if self.stopped:
                metrics.counts.incr('heartbeat_stopped')
                break
            time.sleep(10)
            try:
                metrics.counts.startExecution('heartbeat')
                j = api.onedrive.heartbeat()
                if j == None:                
                    raise Exception('heartbeat failed')

                refresh.thread.trigger(j)                               
            except Exception as e:
                raisedBy = commonfunc.exceptionRaisedBy(e) 
                logger.error(f'heartbeat exception: {raisedBy}')
                if common.offline == False:
                    common.offline = True
                    metrics.counts.incr('heartbeat_offline')
            else:                
                if common.offline:
                    metrics.counts.incr('heartbeat_online')
                    common.offline = False 
            finally:
                metrics.counts.endExecution('heartbeat')       

    def stop(self):
        logger.info('heartbeat_stop')
        metrics.counts.incr('heartbeat_stop')
        self.stopped = True

monitor: Heartbeat = Heartbeat()