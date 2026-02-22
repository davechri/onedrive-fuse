
import errno
import stat

from fuse import FuseOSError

from onedrive_fuse import common, commonfunc, metrics, eventq, metadata
from onedrive_fuse.log import logger

from onedrive_fuse.remote import api, modes
from onedrive_fuse.stats import remoteStats

def execute(path:str, localId: str, mode: int, d: dict[str,any], runAsync: bool=True) -> None:
    logger.debug(f'remote.chmod: path={path} localId={localId} mode={oct(mode)} runAsync={runAsync}')
    if not runAsync and common.offline:
        raise Exception(f'remote.chmod: cannot chmod while offline {path}')    
    
    if runAsync:
        oldMode = d['st_mode']
        d['st_mode'] = mode | ((stat.S_IFDIR | stat.S_IFLNK | stat.S_IFREG) & d['st_mode'])
        if oldMode == d['st_mode']:
            logger.info(f'remote.chmod: path={path} mode is unchanged {oct(mode)}')
            return
        metrics.counts.incr(f'chmod_{oct(oldMode)}_to_{oct(d["st_mode"])}')
        metadata.cache.getattr_save(path, d)

    onedriveId = d.get('onedrive_id', 0)
    if d.get('local_only', False):
        logger.debug(f'remote.chmod: path={path} is local only, not updating Google Drive')
        metrics.counts.incr('chmod_local_only')  
    elif runAsync:
        metrics.counts.incr('chmod_enqueue_event')
        eventq.queue.enqueueChmodEvent(path, localId, onedriveId)    
    else:
        metrics.counts.incr('chmod_network') 
        modes.setMode(d['local_id'], mode)       
        