
import os
from fuse import FuseOSError
import errno
from onedrive_fuse import metadata, common
from onedrive_fuse.log import logger
from onedrive_fuse.remote import api, attr
from onedrive_fuse.threads import tdownload

def execute(path: str) -> str:
    logger.debug(f'remote.readlink: path={path}')
           
    target = metadata.cache.readlink(path)

    if target == None:        
        buf = tdownload.manager.read(path, common.BLOCK_SIZE, 0, readEntireFile=False)
        if buf != None:
            d = metadata.cache.getattr(path)
            target = buf.decode('utf-8')
            metadata.cache.readlink_save(path, d.get('local_id'), target)
    
    if target is None:
        logger.error(f'remote.readlink: path={path} is not a symlink')
        raise Exception(f'remote.readlink: path={path} is not a symlink')
    
    baseDir = os.path.dirname(path)

    logger.debug(f'remote.readlink: {path} baseDir={baseDir} targetPath={target}')   
    return os.path.relpath(target, baseDir)      
    