    
import json
import stat

from onedrive_fuse import common, metadata, lock, directories
from onedrive_fuse.remote import api, attr
from onedrive_fuse.threads import tdownload, tupload
from onedrive_fuse.log import logger

MODES_FILE = '/.onedrive_fuse_modes.json'
DEFAULT_DIR_MODE                 = 0o755
DEFAULT_FILE_MODE_WRITE          = 0o644
DEFAULT_FILE_MODE_NON_READ_ONLY  = 0o444


def setMode(localId: str, mode: int) -> None:   
    logger.info(f'modes.setMode: onedriveId={localId} mode={oct(mode)}')
    with lock.get(MODES_FILE):
        j = getModes()
        
        j[localId] = mode
        api.onedrive.put(MODES_FILE, bytes(json.dumps(j), 'utf-8'))
        
def getModes() -> dict[str, int]:
    d = metadata.cache.getattr(MODES_FILE)
    if d is None:
        buf = api.onedrive.read(MODES_FILE)
        if buf == None:
            logger.info(f'modes.getModes: {MODES_FILE} not found, creating new modes file')
            file = api.onedrive.put(MODES_FILE, bytes(json.dumps({}), 'utf-8'))
            attr.execute(MODES_FILE)
            # mode = getDefaultMode(file)
            # rootDirectory = directories.store.getDirectoryByPath('/')
            # d = attrnew.newAttrFromFile(MODES_FILE, file, rootDirectory, mode, None)
            # metadata.cache.getattr_save(MODES_FILE, d)
    else:
        buf = tdownload.manager.read(MODES_FILE, common.BLOCK_SIZE, 0, readEntireFile=False)

    if buf != None and len(buf) == 0:
        buf = None
    try:
        j = json.loads(buf.decode('utf-8')) if buf is not None else {}
    except json.JSONDecodeError as e:
        logger.error(f'modes.getModes: {MODES_FILE} JSON decode error: {e} json={buf}')
        j = {}
        api.onedrive.delete(MODES_FILE)
          
    logger.info(f'modes.getModes: {len(j)}')
    return j

def deleteMode(localId: str) -> None:
    j = getModes()
    if localId not in j:
        logger.debug(f'modes.deleteMode: localId={localId} not found in modes file')
        return    
    with lock.get(MODES_FILE):
        j = getModes()
        if localId in j:
            logger.info(f'modes.deleteMode: localId={localId}')
            del j[localId]
            api.onedrive.put(MODES_FILE, bytes(json.dumps(j), 'utf-8'))
            
executable_types = (
    'application/octet-stream',
    'application/x-csh',
    'application/x-sh',
    'application/x-tcl',
)

def getDefaultMode(file) -> int:        
        mimeType = file.get('mimeType')        
        if file.get('mimeType') == 'folder':
            mode = DEFAULT_DIR_MODE
        else:
            mode = DEFAULT_FILE_MODE_WRITE
         
            if mimeType in executable_types:
                mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH

        if mimeType == 'folder':                       
            mode = (stat.S_IFDIR | mode)                
        elif file.get('mimeType') == '???':
            mode = (stat.S_IFLNK | mode)
        else:
            mode = (stat.S_IFREG | mode)
        
        return mode      