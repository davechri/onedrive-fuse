import io
from fuse import FuseOSError
import errno
import stat

from onedrive_fuse import common, metrics, eventq, metadata
from onedrive_fuse.log import logger
from onedrive_fuse.remote import modes

from onedrive_fuse.threads import tdelete

def execute(path: str) -> None:  
    logger.debug(f'remote.rmdir: path={path}')

    d = metadata.cache.getattr(path)
    if d is None:
        raise FuseOSError(errno.ENOENT) 
    
    if d["st_mode"] & stat.S_IFDIR == 0:
        metrics.counts.incr('rmdir_not_directory')
        raise FuseOSError(errno.ENOTDIR)

    dirEntries =metadata.cache.readdir(path)
    if dirEntries is not None and len(dirEntries) > 2:
        metrics.counts.incr('rmdir_not_empty')
        raise FuseOSError(errno.ENOTEMPTY)
    
    if not d.get('local_only', False):
        metrics.counts.incr('rmdir_enqueue_event')
        eventq.queue.enqueueDirEvent(path, d["local_id"], d["onedrive_id"])
    else:
        logger.debug(f'remote.rmdir: path={path} is local only, not removing directory from Google Drive')
        metrics.counts.incr('rmdir_local_only')

    metadata.cache.deleteMetadata(path, d["local_id"], 'remote.rmdir: directory removed')
       
       
        
    