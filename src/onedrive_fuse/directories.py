import copy
import json
import os
from onedrive_fuse import db, metrics
from onedrive_fuse.log import logger

DIRECTORY_PREFIX = 'directory'

class Directory:
    def __init__(self, onedriveId: int, localId: str, path: str, name: str, localParentId: str | None, localOnly: bool):        
        self.onedriveId = onedriveId
        self.localId = localId
        self.path = path
        self.name = name       
        self.localParentId = localParentId
        self.localParentId = localParentId
        self.localOnly = localOnly

class Directories:
    def __init__(self):        
        self.directoriesByPath: dict[str,Directory] = {} # key=path, value=Directory
        self.directoriesByLocalId: dict[str,Directory] = {} # key=id, value=Directory
        self.directoriesByOnedriveId: dict[str,Directory] = {} # key=onedriveId, value=Directory

        self.localOnlyDirectoriesByPath: dict[str,Directory] = {} # key=path, value=Directory
        self.localOnlyDirectoriesByLocalId: dict[str,Directory] = {} # key=id, value=Directory

    def key(self, localId: str) -> str:
        return f'{DIRECTORY_PREFIX}:{localId}'

    def size(self) -> int:
        return len(self.directoriesByPath) + len(self.localOnlyDirectoriesByPath) 
    
    def getAllDirectories(self) -> list[Directory]:
        return list(self.directoriesByPath.values()) + list(self.localOnlyDirectoriesByPath.values())
    
    def _putDirectory(self, directory: Directory):        
        key = self.key(directory.localId)
        value = {    
            'onedriveId': directory.onedriveId,       
            'local_id': directory.localId,
            'path': directory.path,
            'name': directory.name,            
            'local_parent_id': directory.localParentId,
            'local_only': directory.localOnly
        }
        logger.debug(f'directories.putDirectory {value}')
        db.cache.put(key, bytes(json.dumps(value), encoding='utf-8'), DIRECTORY_PREFIX)        
       
    def populateFromDb(self):
        logger.info('directories.populateFromDb')
        directoriesByPath: dict[str,Directory] = {} # key=path, value=Directory
        directoriesByLocalId: dict[str, Directory] = {} # key=id
        directoriesByOnedriveId: dict[str, Directory] = {} # key=onedriveId

        doUpdate = False
        it = db.cache.getIterator()
        doUpdate = False
        for key, value in it(prefix=bytes(DIRECTORY_PREFIX, encoding='utf-8')):
            d = json.loads(value) 
            directory = Directory(
                d['onedriveId'],
                d['local_id'], 
                d['path'], 
                d['name'], 
                d['local_parent_id'],
                d['local_only']
            )       
            logger.debug(f'directories.populateFromDb: {d}')
            if directory.localOnly:
                self.localOnlyDirectoriesByPath[directory.path] = directory
                self.localOnlyDirectoriesByLocalId[directory.localId] = directory
            else:
                directoriesByPath[directory.path] = directory                
                directoriesByLocalId[directory.localId] = directory
                directoriesByOnedriveId[directory.onedriveId] = directory
                doUpdate = True
        if doUpdate:
            self.update(directoriesByPath, directoriesByLocalId, directoriesByOnedriveId)    

    def update(self, directoriesByPath: dict[str,Directory], directoriesByLocalId: dict[str,Directory], directoriesByOnedriveId: dict[str,Directory]|None=None):
        if self.directoriesByPath != directoriesByPath:            
            # atomic update:
            self.directoriesByPath = directoriesByPath            
            self.directoriesByLocalId = directoriesByLocalId
            self.directoriesByOnedriveId = directoriesByOnedriveId if directoriesByOnedriveId is not None else {}
            metrics.counts.incr('directories_updated')

    def getDirectoryByPath(self, path: str) -> Directory | None:
        """
        Retrieves a Directory object corresponding to the given path.
        Args:
            path (str): The path of the directory to retrieve.
        Returns:
            Directory: The Directory object if found; otherwise, None.
        """
        directory = None
        if path in self.directoriesByPath:
            directory = self.directoriesByPath[path]
        elif path in self.localOnlyDirectoriesByPath:
            directory = self.localOnlyDirectoriesByPath[path]
        else:
            metrics.counts.incr('directories_by_path_miss')
        logger.debug(f'directories.getDirectoryByPath: path={path} {directory.__dict__ if directory else "not found"}')
        return directory

    def getDirectoryByLocalId(self, localId: str) -> Directory | None:
        if localId in self.directoriesByLocalId:
            return self.directoriesByLocalId[localId]
        elif localId in self.localOnlyDirectoriesByLocalId:
            return self.localOnlyDirectoriesByLocalId[localId]
        else:
            metrics.counts.incr('directories_by_local_id_miss')
            return None    
        
    def getDirectoryByOnedriveId(self, onedriveId: str) -> Directory | None:
        if onedriveId in self.directoriesByOnedriveId:
            return self.directoriesByOnedriveId[onedriveId]        
        else:
            metrics.counts.incr('directories_by_onedrive_id_miss')
            return None    
        
    def getParentDirectory(self, path: str) -> Directory | None:
        parentDir = self.getDirectoryByPath(os.path.dirname(path))        
        metrics.counts.incr('directories_get_parent_miss') if parentDir == None else None 
        return parentDir
    
    def renameDirectory(self, oldPath: str, newPath:str, recursive: bool=True):
        directory = self.getDirectoryByPath(oldPath)
        
        if directory is not None:
            # Update the directory's path and name
            directory.path = newPath
            directory.name = os.path.basename(newPath)

            # Update the directoriesByPath mappings
            if directory.localOnly:
                self.localOnlyDirectoriesByPath[newPath] = directory
                del self.localOnlyDirectoriesByPath[oldPath]
                self.localOnlyDirectoriesByLocalId[directory.localId] = directory
                dbp = copy.deepcopy(self.localOnlyDirectoriesByPath) 
            else:
                self.directoriesByPath[newPath] = directory
                del self.directoriesByPath[oldPath]
                self.directoriesByLocalId[directory.localId] = directory
                self.directoriesByOnedriveId[directory.onedriveId] = directory
                dbp = copy.deepcopy(self.directoriesByPath) 

            for path, dir in dbp.items():
                if dir.localParentId == directory.localId and path.startswith(oldPath + '/'):
                    subDirNewPath = newPath + path[len(oldPath):]
                    self.renameDirectory(path, subDirNewPath, recursive=False)

            metrics.counts.incr('directories_rename')
            logger.info('directories.rename: old=%s new=%s local_id=%s local_parent_id=%s', oldPath, newPath, directory.localId, directory.localParentId)
        else:
            metrics.counts.incr('directories_rename_not_found')
            logger.info('directories.rename: old=%s new=%s', oldPath, newPath)

    def addDirectory(self, path: str, onedriveId: int, localId: str, localParentId: str, localOnly: bool):
        name = os.path.basename(path)
        directory = Directory(onedriveId, localId, path, name, localParentId, localOnly=localOnly)        
        
        if localOnly:
            self.localOnlyDirectoriesByPath[path] = directory
            self.localOnlyDirectoriesByLocalId[localId] = directory
        else:
            self.directoriesByPath[path] = directory  
            self.directoriesByLocalId[localId] = directory
            self.directoriesByOnedriveId[onedriveId] = directory

        self._putDirectory(directory)     
        logger.info('directories.add: %s local_id=%s onedriveId=%s local_parent_id=%s', path, directory.localId, directory.onedriveId, directory.localParentId)
        metrics.counts.incr('directories_add')

    def deleteDirectoryByLocalId(self, path: str, localId: str):        
        directory = self.getDirectoryByLocalId(localId)

        if directory is not None:
            # Remove the directory fronnm the mappings
            if directory.localOnly:
                del self.localOnlyDirectoriesByPath[directory.path]
                del self.localOnlyDirectoriesByLocalId[directory.localId]
            else:
                del self.directoriesByPath[directory.path]
                del self.directoriesByLocalId[directory.localId]
                if directory.onedriveId in self.directoriesByOnedriveId:
                    del self.directoriesByOnedriveId[directory.onedriveId]

            metrics.counts.incr('directories_delete')
            logger.info('directories.delete: %s local_id=%s local_parent_id=%s', directory.path, directory.localId, directory.localParentId) 
            db.cache.delete(self.key(directory.localId), DIRECTORY_PREFIX)
        else:
            logger.warning('directories.delete: directory not found: %s %s', path, localId)
            metrics.counts.incr('directories_delete_not_found')      

store: Directories = Directories()