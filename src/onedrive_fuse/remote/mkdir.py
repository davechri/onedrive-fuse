
import errno

import os
from pathlib import Path
import stat
import time

from fuse import FuseOSError

from onedrive_fuse import common, commonfunc, directories, localonly, metadata, gitignore, metrics, eventq
from onedrive_fuse.log import logger
from onedrive_fuse.remote import api, attr, attrnew, modes
from onedrive_fuse.stats import remoteStats

def execute(path: str, mode: int, runAsync: bool=True) -> str:
    logger.debug(f'remote.mkdir: path={path} mode={oct(mode)} runAsync={runAsync}')

    if not runAsync and common.offline:
        raise Exception(f'remote.mkdir: cannot mkdir while offline {path}')

    if path == '/':
        raise FuseOSError(errno.ENOENT)
    
    # Check if directory already exists and not called by eventq.queue.enqueueDirEvent
    if directories.store.getDirectoryByPath(path) != None and runAsync == True:       
        raise FuseOSError(errno.EEXIST)
    
    parentDirectory = directories.store.getParentDirectory(path) 
    if parentDirectory == None:
        raise FuseOSError(errno.ENOENT)

    if localonly.lconfig.isInLocalOnlyConfig(path):
        logger.info(f'remote.mkdir: localOnly {path}')
        if not parentDirectory.localOnly:
            localonly.lconfig.createDirSymLink(path)
            return None
        
    localOnly = False
    localId = metadata.cache.getattr_get_local_id(path)
    if localId == None:
        localOnly = gitignore.parser.isIgnored(path)                
   
    mode2 = stat.S_IFDIR | mode
    
    onedriveId = 0       
    if not runAsync and not localOnly:        
        for timeout in commonfunc.apiTimeoutRange():
            try:  
                metrics.counts.incr('mkdir_network')
                if parentDirectory.onedriveId == 0:                   
                    raise Exception('remote.mkdir: parentDirectory.onedriveId is 0 for path=%s %s', path, parentDirectory.__dict__)
                remoteStats.mkdir += 1
                file = api.onedrive.mkdir(os.path.dirname(path), os.path.basename(path))                
                break
            except FuseOSError as e:
                if e.errno == errno.EEXIST:
                    logger.info(f'remote.mkdir: directory already exists {path}')
                    d = attr.execute(path)   
                    onedriveId = d.get('onedrive_id', 0)
                    break
                elif e.errno == errno.ENOENT:
                    raise
                else:
                    logger.error(f'remote.mkdir FuseOSError: {e} for path={path} attempt with timeout {timeout}')
                    metrics.counts.incr('mkdir_fuseoserror')
                    if commonfunc.isLastAttempt(timeout):
                        raise
            except TimeoutError as e:
                logger.error(f'mkdir timeout: {e}')
                metrics.counts.incr('mkdir_network_timeout')
                if commonfunc.isLastAttempt(timeout):
                    raise

        if mode2 != modes.getDefaultMode(file):        
                modes.setMode(localId, mode2)    
        d = attr.execute(path)   
        d['onedrive_id'] = file['id']           
        onedriveId = file['id']
    else:
        d = attrnew.newAttr(path, 4096, mode2, parentDirectory, localOnly, localId=localId)
        localId = d.get('local_id')
        if localOnly:            
            metrics.counts.incr('mkdir_localonly')
        else:            
            metrics.counts.incr('mkdir_enqueue_event')        
            eventq.queue.enqueueDirEvent(path, localId, onedriveId=0)
   
    metadata.cache.getattr_save(path, d)

    parentPath = parentDirectory.path
    metadata.cache.readdir_add_entry(parentPath, d.get('file_name'), localId)
        
    directories.store.addDirectory(path, d.get('onedrive_id'), d.get('local_id'), parentDirectory.localId, localOnly)

    metadata.cache.readdir_add_entry(path, '.', d.get('local_id'))
    metadata.cache.readdir_add_entry(path, '..', parentDirectory.localId)
    
    return onedriveId
