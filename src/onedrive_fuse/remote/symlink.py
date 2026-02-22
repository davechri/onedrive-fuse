
import os
import time
import errno
import stat

from fuse import FuseOSError

from onedrive_fuse.stats import remoteStats
from onedrive_fuse import common, commonfunc, directories, gitignore, metadata, eventq, metrics
from onedrive_fuse.log import logger
from onedrive_fuse.remote import attrnew, attr, api, modes

def execute(source: str, sourceLocalId: str | None, target: str, runAsync: bool=True) -> None:    
    logger.info(f'remote.symlink: source={source} sourceLocalId={sourceLocalId} target={target} runAsync={runAsync}')
    if not runAsync and common.offline:
        raise Exception(f'remote.symlink: cannot symlink while offline {source}')
    
    parentDirectory = directories.store.getParentDirectory(source)
    if parentDirectory == None:
        logger.error(f'remote.symlink: parentDirectory is None for source={source}')
        raise FuseOSError(errno.ENOENT)
    
    localOnly = sourceLocalId != None and commonfunc.isInLocalOnlyConfigLocalId(sourceLocalId) or gitignore.parser.isIgnored(source)   
       
    mode = stat.S_IFLNK | 0o511
   
    if not runAsync and not localOnly:
        metrics.counts.incr('symlink_network')
        remoteStats.symlink += 1
        file = api.onedrive.put(source, bytes(target, 'utf-8'))        
        d = attr.execute(source)                
        d['onedrive_id'] = file['id']   
        onedriveId = file['id']
        modes.setMode(d['local_id'], mode)       
    else:
        d = attrnew.newAttr(source, len(target), mode, parentDirectory, localOnly, localId=sourceLocalId if sourceLocalId != None else commonfunc.generateLocalId(source, 'symlink', 'remote.symlink', localOnly=localOnly))
        localId = d.get('local_id')
        if not localOnly:
            metrics.counts.incr('symlink_enqueue_event')   
            eventq.queue.enqueueSymlinkEvent(source, localId, onedriveId=0)
        else:
            metrics.counts.incr('symlink_localonly')    
    
        metadata.cache.getattr_save(source, d)
        parentPath = parentDirectory.path
        localId = d.get('local_id')
        metadata.cache.readdir_add_entry(parentPath, os.path.basename(source), localId)
        metadata.cache.readlink_save(source, localId, target)
       