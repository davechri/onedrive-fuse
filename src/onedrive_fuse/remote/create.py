
from datetime import datetime
import errno

import os
import stat
import time
from pathlib import Path
from onedrive_fuse import common, commonfunc, directories, gitignore, metrics, eventq, metadata
from onedrive_fuse.log import logger
from onedrive_fuse.remote import api, attr, attrnew, modes
from fuse import FuseOSError

from onedrive_fuse.stats import remoteStats

def execute(path: str, mode: int, localId: str|None = None, runAsync: bool=True) -> None:
    """
    Creates a new file at the specified path with the given mode.
    Args:
        path (str): The path where the new file will be created.
        mode (int): The file mode (permissions) for the new file.
    Returns:
        str: The ID of the newly created file in Google Drive.
    Raises:
        FuseOSError: If an error occurs during file creation.
    """ 
    logger.debug(f'remote.create: path={path} mode={oct(mode)} localId={localId} runAsync={runAsync}')

    if not runAsync and common.offline:
        raise Exception(f'remote.create: cannot create while offline {path}')
    
    if path == '/':
        raise FuseOSError(errno.EINVAL)
   
    parentDirectory = directories.store.getParentDirectory(path)    
    if parentDirectory == None:
        raise FuseOSError(errno.ENOENT)

    d = metadata.cache.getattr(path, localId)
    if d != None:
        localId = d.get('local_id')
        if d.get('onedrive_id', 0) != 0:
            metrics.counts.incr('create_truncate')
            if not runAsync:    
                remoteStats.truncate += 1  
                file = api.onedrive.put(path, b'') # createdDateTime,lastModifiedDateTime
                d['st_atime'] = datetime.fromisoformat(file['lastModifiedDateTime']).timestamp()  
                d['st_mtime'] = datetime.fromisoformat(file['lastModifiedDateTime']).timestamp()
                d['st_ctime'] = datetime.fromisoformat(file['createdDateTime']).timestamp()
                metadata.cache.getattr_save(path, d)   
            elif not d.get('local_only'):
                metrics.counts.incr('create_truncate_enqueue_event')
                eventq.queue.enqueueTruncateEvent(path, d.get('local_id'), d.get('onedrive_id'), 0)
            else:
                logger.debug(f'remote.create: path={path} is local only, not truncating on Google Drive')
                metrics.counts.incr('create_truncate_local_only')
            d['st_size'] = 0
            metadata.cache.getattr_save(path, d)
            return
    
    localOnly = False
    if localId == None:
        if gitignore.parser.isIgnored(path):
            logger.debug(f'remote.create: {path} is ignored by .gitignore, creating as local only')
            localOnly = True
        
    name = os.path.basename(path)   
    mode = stat.S_IFREG | mode      
    onedriveId = 0       
    if not runAsync and not localOnly:
        for timeout in commonfunc.apiTimeoutRange():
            try:
                metrics.counts.incr('create_network')  
                if parentDirectory.onedriveId == 0:                    
                    raise Exception('remote.create: parentDirectory.onedriveId is 0 for path=%s', path)

                remoteStats.create += 1
                file = api.onedrive.put(path, b'') # createdDateTime,lastModifiedDateTime  
                if mode != modes.getDefaultMode(file):
                    modes.setMode(d['local_id'], mode)              
                d = attr.execute(path)
                d['onedrive_id'] = file['id']   
                onedriveId = file['id']
                break
            except TimeoutError as e:
                logger.error(f'create timed out: {e}')
                metrics.counts.incr('create_network_timeout')
                if commonfunc.isLastAttempt(timeout):
                    raise
    else:
        metrics.counts.incr('create_enqueue_event')
        if d == None:
            d = attrnew.newAttr(path, 0, mode, parentDirectory, localOnly, localId=localId)
            localId = d.get('local_id')
            metadata.cache.getattr_save(path, d)        
       
    if localOnly:            
        metrics.counts.incr('create_localonly')
    else:
        eventq.queue.enqueueFileEvent(path, localId, onedriveId=0)
        logger.debug('remote.create enqueue event: %s', d)

    parentPath = parentDirectory.path
    metadata.cache.readdir_add_entry(parentPath, name, localId)
    
    return onedriveId
    