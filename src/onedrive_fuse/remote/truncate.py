
import errno

from fuse import FuseOSError

from onedrive_fuse import common, commonfunc, data, metrics, eventq, metadata
from onedrive_fuse.log import logger
from onedrive_fuse.remote import api, attr
from onedrive_fuse.stats import remoteStats

def execute(path: str, localId: str, size: int, d: dict[str,any], runAsync: bool=True) -> int: 
    logger.debug(f'remote.truncate: path={path} localId={localId} size={size} runAsync={runAsync}')
    if not runAsync and common.offline:
        raise Exception(f'remote.truncate: cannot truncate while offline {path}')

    d = metadata.cache.getattr(path, localId)
    if d == None:
        raise FuseOSError(errno.ENOENT)    
     
    if not runAsync:        
        metrics.counts.incr('truncate_network')
        
        for timeout in commonfunc.apiTimeoutRange():
            try:   
                remoteStats.truncate += 1   
                api.onedrive.put(path, b'\0' * size)
                break
            except TimeoutError as e:
                logger.error(f'truncate timeout {e}')
                metrics.counts.incr('truncate_network_timeout')
                if commonfunc.isLastAttempt(timeout):
                    raise 
    elif d.get('local_only'):
        logger.debug(f'remote.truncate: path={path} is local only, not truncating on Google Drive')
        metrics.counts.incr('truncate_local_only')
    else:
        metrics.counts.incr('truncate_enqueue_event')
        eventq.queue.enqueueTruncateEvent(path, localId, d.get('gd_id'))
    
    d['st_size'] = size
    metadata.cache.getattr_save(path, d)
    if size == 0:
        data.cache.deleteByID(path, localId)

    return size
    