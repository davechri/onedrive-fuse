import os
from fuse import FuseOSError
import errno
import stat
from onedrive_fuse import common, data, metrics, eventq, metadata, localonly
from onedrive_fuse.log import logger
from onedrive_fuse.remote import modes

def execute(path: str) -> None:
   
    d = metadata.cache.getattr(path)
    if d is None:
        raise FuseOSError(errno.ENOENT)    
    
    if not d.get('local_only', False):
        metrics.counts.incr('remove_enqueue_event')
        eventq.queue.enqueueFileEvent(path, d.get('local_id'), d.get('onedrive_id', 0))        
    else:
        logger.debug(f'remote.remove: path={path} is local only, not removing from remote server')
        metrics.counts.incr('remove_local_only')
        if d.get('st_mode') & stat.S_IFLNK == stat.S_IFLNK:
            localonly.lconfig.deleteDirSymLink(path)  
           
    data.cache.deleteByID(path, d.get('local_id'))
    metadata.cache.deleteMetadata(path, d.get('local_id'), 'remove: delete metadata')
 