
from onedrive_fuse import directories, eventq, common
from onedrive_fuse.remote import readdir
from onedrive_fuse.log import logger

firstRefresh = True

def refreshDelta(delta: dict[str, any]) -> None:
    global firstRefresh
    logger.info('refreshcache.refreshDelta: starting full refresh of cache')

    if common.offline:
        logger.info('refreshcache.refreshDelta: skipping refresh due to offline mode')
        return
    if eventq.eventCount > 0:
        logger.info('refreshcache.refreshDelta: stopping refresh due to queued events')
        return
    
    for file in delta.get('value', []):
        if file.get('folder', None) == None:
            continue
        directory = directories.store.getDirectoryByOnedriveId(file.get('id'))
        if directory != None:
            readdir.execute(directory.path, deleteEntries=True)        