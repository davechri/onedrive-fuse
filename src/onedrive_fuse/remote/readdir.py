
import errno
import copy

import os

import stat
from fuse import FuseOSError
from requests import delete
from onedrive_fuse import common, commonfunc, directories, directories, metadata, lock, eventq, metrics, data
from onedrive_fuse.log import logger
from onedrive_fuse.remote import api, attrnew, modes
from onedrive_fuse.stats import remoteStats

def execute(path: str, deleteEntries=False) -> dict[str, str]:
    logger.info(f'remote.readdir {path}')
    if common.offline:
        metrics.counts.incr('readdir_offline')
        raise FuseOSError(errno.ENETDOWN)
    metrics.counts.incr('readdir_network')
    modesDic: dict[str, int] = {}    
        
    remoteStats.readdir += 1
    # id,name,createdDateTime,lastModifiedDateTime,size,file,folder
    children = api.onedrive.readdir(path)  
    modesDic = modes.getModes()    
     
    if children is None or len(children) == 0:   
        logger.info(f'remote.readdir not found {path}')
        if deleteEntries:
            oldEntries = metadata.cache.readdir(path)
            if oldEntries is not None:
                for name, onedriveId in oldEntries.items():
                    logger.info(f'remote.readdir deleting removed entry {name} from {path}')                    
                    modes.deleteMode(onedriveId)
                    data.cache.deleteByID(os.path.join(path, name), onedriveId)               
                    metadata.cache.deleteMetadata(os.path.join(path, name), onedriveId, 'remote.readdir: entry removed')
                onedriveId = metadata.cache.getattr_get_local_id(path)
                if onedriveId:
                    metadata.cache.deleteMetadata(path, onedriveId, 'remote.readdir: directory removed')
            return {}
        else:
            logger.info(f'remote.readdir: directory not found in cache for {path}')
            raise FuseOSError(errno.ENOENT)
        
    logger.debug(f'remote.readdir api returned children for {path}') 
    with lock.get(path):    
        logger.info(f'remote.readdir processing {len(children)} children for {path}')    
        dirEntries = metadata.cache.readdir(path)
        oldEntries = copy.deepcopy(dirEntries) if deleteEntries and dirEntries is not None else {}    
        if dirEntries == None:
            dirEntries: dict[str, str] = {}
           
        thisDirectory = directories.store.getDirectoryByPath(path) 
        if thisDirectory == None:
            parentDirectory = directories.store.getParentDirectory(os.path.dirname(path))           
            thisDirectory = directories.Directory(
                onedriveId=0,
                localId=commonfunc.generateLocalId(path, 'dir', 'readdir', localOnly=False),
                path=path,
                name=os.path.basename(path) if path != '/' else '/',
                localParentId=parentDirectory.localId if parentDirectory != None else None,
                localOnly=False
            )
            directories.store.addDirectory(
                thisDirectory.path,
                thisDirectory.onedriveId,
                thisDirectory.localId,
                thisDirectory.localParentId,
                thisDirectory.localOnly
            )
        parentDirectory = directories.store.getParentDirectory(path)

        i = 0
        for file in children:            
            if i == 0:  # first line is the directory itself
                file['name'] = '.'
            i += 1          
            
            onedriveId = file['id']
            if onedriveId in modesDic:
                mode = modesDic[onedriveId]
            else:         
                mode = modes.getDefaultMode(file)
            name = file.get('name', 'unknown')            

            logger.info(f'remote.readdir found entry: file={file}')
            if name == '.':  
                if thisDirectory.onedriveId == 0:
                    thisDirectory.onedriveId = onedriveId                    
                    directories.store.addDirectory(
                        thisDirectory.path,
                        thisDirectory.onedriveId,
                        thisDirectory.localId,
                        thisDirectory.localParentId,
                        thisDirectory.localOnly
                    )
                        
                dirEntries[name] = thisDirectory.localId
                oldEntries.pop(name, None)

                d = metadata.cache.getattr(path)
                if d is not None:
                    if d['onedrive_id'] != onedriveId:
                        if d['onedrive_id'] == 0:
                            logger.info(f'remote.readdir: setting onedriveId for {path} to {onedriveId}')
                        else:
                            logger.warning(f'remote.readdir: updating onedriveId for {path} from {d["onedrive_id"]} to {onedriveId}')
                        d['onedrive_id'] = onedriveId
                        metadata.cache.getattr_save(path, d)
                    continue   
                d = attrnew.newAttrFromFile(path, file, parentDirectory, mode, thisDirectory.localId)
                metadata.cache.getattr_save(path, d)
                continue
            else:
                childPath = os.path.join(path, name)
                d = metadata.cache.getattr(childPath)  
                if d is not None:
                    if d['onedrive_id'] != onedriveId:
                        if d['onedrive_id'] == 0:
                            logger.info(f'remote.readdir: setting onedriveId for {childPath} to {onedriveId}')
                        else:
                            logger.warning(f'remote.readdir: updating onedriveId for {childPath} from {d["onedrive_id"]} to {onedriveId}')
                        d['onedrive_id'] = onedriveId
                        metadata.cache.getattr_save(childPath, d)
                    dirEntries[name] = d['local_id']  
                    oldEntries.pop(name, None)
                    continue 
            
            # If an event is queued for this onedriveId, do not update the directory entry.        
            if onedriveId != 0 and eventq.queue.isEventQueuedForInode(onedriveId):
                logger.info(f'remote.readdir: skipping entry {childPath} because an event is queued for onedriveId {onedriveId}')
                continue

            directory = None
            if file.get('mimeType') == 'folder':
                directory = directories.store.getDirectoryByOnedriveId(onedriveId)
            d = attrnew.newAttrFromFile(os.path.join(path, name), file, parentDirectory, mode, directory.localId if directory is not None else None)
        
            if 'folder' in file:                
                directories.store.addDirectory(
                    os.path.join(path, name), 
                    d['onedrive_id'], 
                    d['local_id'], 
                    d['local_parent_id'], 
                    localOnly=False)

            metadata.cache.getattr_save(os.path.join(path, name), d)
            dirEntries[name] = d['local_id']   
            oldEntries.pop(name, None)            
        if path != '/':
            dirEntries['..'] = thisDirectory.localParentId
            oldEntries.pop('..', None)

        if deleteEntries:
            for name, onedriveId in oldEntries.items():
                logger.info(f'remote.readdir deleting removed entry {name} from {path}')                
                modes.deleteMode(onedriveId)   
                data.cache.deleteByID(os.path.join(path, name), onedriveId)                        
                metadata.cache.deleteMetadata(os.path.join(path, name), onedriveId, 'remote.readdir: entry removed')

        logger.info(f'remote.readdir {path} entries={dirEntries}')
        metadata.cache.readdir_save(path, dirEntries) 

    return dirEntries

ST_SIZE_DIR = 4096

