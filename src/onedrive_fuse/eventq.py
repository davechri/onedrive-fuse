
import stat
import json

from onedrive_fuse import common, commonfunc, db, log, metrics, metadata, gitignore
from onedrive_fuse.log import logger
from onedrive_fuse.remote import attr, chmod, mkdir, create, rename, symlink, truncate
from onedrive_fuse.threads import tdelete, tupload, tcreate

FILE_EVENT = 'file'
DIR_EVENT = 'dir'
RENAME_EVENT = 'rename'
SYMLINK_EVENT = 'symlink'
CHMOD_EVENT = 'chmod'
TRUNCATE_EVENT = 'truncate'

EVENT_PREFIX = 'event'
EVENT_SEQ_NUM_KEY = '_eventseq'

eventCount = 0

class Key:
    def __init__(self, event: str, fromOperation: str, seqNum: int):
        self.event = event
        self.fromOperation = fromOperation
        self.seqNum = seqNum

class Value:
    def __init__(self, path: str, path2: str|None, localId: str, onedriveId: int, failedCount: int, retryCount: int):
        self.path = path
        self.path2 = path2
        self.localId = localId
        self.onedriveId = onedriveId
        self.failedCount = failedCount
        self.retryCount = retryCount
        
class RetryException(Exception):
    def __init__(self, message: str):
        super().__init__(message)

class EventQueue:
    def init(self):
        seqNum = db.cache.get(EVENT_SEQ_NUM_KEY, EVENT_PREFIX) 
        if seqNum == None:
            seqNum = 0
        else:
            seqNum = int(str(seqNum, 'utf-8'))       
        self.seqNum = seqNum
        global eventCount
        it = db.cache.getIterator()
        for key, value in it(prefix=bytes(EVENT_PREFIX, 'utf-8')):
            eventCount += 1
        logger.info(f'init: seqNum={self.seqNum} eventCount={eventCount}')
    
    def key(self, event: str, fromOperation: str, seqNum: int) -> str:
        return f'{EVENT_PREFIX}:{seqNum:010d}:{event.ljust(10)}:{fromOperation.ljust(10)}'
    
    def enqueueFileEvent(self, path: str, localId: str, onedriveId: int) -> None:
        self.enqueueEvent(path, None, localId, onedriveId, FILE_EVENT)

    def enqueueDirEvent(self, path: str, localId: str, onedriveId: int) -> None:
        self.enqueueEvent(path, None, localId, onedriveId, DIR_EVENT)
    def enqueueRenameEvent(self, oldpath: str, newPath: str, localId: str, onedriveId: int) -> None:
        if newPath is None:
            raise Exception(f'enqueueRenameEvent: {oldpath} newPath cannot be None')
        self.enqueueEvent(oldpath, newPath, localId, onedriveId, RENAME_EVENT)
    def enqueueSymlinkEvent(self, path: str,  localId: str, onedriveId: int) -> None:
        self.enqueueEvent(path, None, localId, onedriveId, SYMLINK_EVENT)

    def enqueueChmodEvent(self, path: str, localId: str, onedriveId: int) -> None:
        self.enqueueEvent(path, None, localId, onedriveId, CHMOD_EVENT)
    def enqueueTruncateEvent(self, path: str, localId: str, onedriveId: int) -> None:
        self.enqueueEvent(path, None, localId, onedriveId, TRUNCATE_EVENT)

    def enqueueEvent(self, path: str, path2: str|None, localId: str, onedriveId: int, event: str) -> None:
        fromOperation = common.threadLocal.operation
        metrics.counts.incr(f'enqueue_{fromOperation}_{event}_event')
        self.seqNum += 1
        logger.info(f'enqueue {event} event: {path} {path2} local_id={localId} onedriveId={onedriveId} seqNum={self.seqNum}')
        data = {
            'path': path,
            'path2': path2,
            'local_id': localId,
            'onedriveId': onedriveId
        }
        db.cache.put(self.key(event, fromOperation, self.seqNum), bytes(json.dumps(data), 'utf-8'), EVENT_PREFIX)
        db.cache.put(EVENT_SEQ_NUM_KEY, bytes(str(self.seqNum), 'utf-8'), EVENT_PREFIX)
        global eventCount
        eventCount += 1
    
    def parseKey(self, key: bytes) -> Key:
        key = str(key, 'utf-8')
        (_, seqNum, event, fromOperation) = key.split(':', 4)
        return Key(event.strip(), fromOperation.strip(), int(seqNum))
    
    def parseValue(self, value: bytes) -> Value:
        data = json.loads(value)
        path = data.get('path')
        path2 = data.get('path2')
        localId = data.get('local_id')
        onedriveId = data.get('onedriveId', 0)
        failedCount = data.get('failed_count', 0)
        retryCount = data.get('retry_count', 0)
        return Value(path, path2, localId, onedriveId, failedCount, retryCount)
    
    def isEventQueuedForInode(self, onedriveId: int) -> bool:
        it = db.cache.getIterator()
        for _, value in it(prefix=bytes(EVENT_PREFIX, 'utf-8')):
            data = json.loads(value)  
            onedriveId2 = data.get('onedriveId', 0)
            logger.debug(f'isEventQueuedForInode: checking onedriveId={onedriveId2} against onedriveId={onedriveId} {data}')
            if onedriveId2 == onedriveId:
                return True            
        return False

    def executeEvents(self) -> None:
        metrics.counts.incr('execute_events')
        logger.info('executeEvents: start')
        saveOperation = common.threadLocal.operation
        savePath = common.threadLocal.path
        try:
            common.threadLocal.operation = 'eventq.py'
            common.threadLocal.path = None
            files: list[tuple[str, str]] = []
            it = db.cache.getIterator()
            for key, value in it(prefix=bytes(EVENT_PREFIX, 'utf-8')):
                key = str(key, 'utf-8')
                (_, seqNum, event, fromOperation) = key.split(':', 4)
                event = event.strip()
                fromOperation = fromOperation.strip()
                data = json.loads(value)
                path = data.get('path')
                path2 = data.get('path2')
                localId = data.get('local_id')
                onedriveId = data.get('onedriveId', 0)
                failedCount = data.get('failed_count', 0)  
                retryCount = data.get('retry_count', 0)              
                exception = None
                try:                   
                    common.threadLocal.path = (path,)                    
                    if event == FILE_EVENT:
                        common.threadLocal.operation = f'{fromOperation}_file_event'
                        self.executeFileEvent(path, localId, onedriveId, seqNum, fromOperation)
                    elif event == DIR_EVENT:
                        common.threadLocal.operation = f'{fromOperation}_dir_event'
                        self.executeDirEvent(path, localId, onedriveId, seqNum, fromOperation)  
                    elif event == RENAME_EVENT:  
                        op = fromOperation + '_' if fromOperation != 'rename' else ''
                        common.threadLocal.operation = f'{op}rename_event'
                        self.executeRenameEvent(path, path2,localId, onedriveId, seqNum, fromOperation)               
                    elif event == SYMLINK_EVENT:
                        op = fromOperation + '_' if fromOperation != 'symlink' else ''
                        common.threadLocal.operation = f'{op}symlink_event'
                        self.executeSymlinkEvent(path, localId, onedriveId, seqNum, fromOperation)  
                    elif event == CHMOD_EVENT:
                        op = fromOperation + '_' if fromOperation != 'chmod' else ''
                        common.threadLocal.operation = f'{op}chmod_event'
                        self.executeChmodEvent(path, localId, onedriveId, seqNum, fromOperation)
                    elif event == TRUNCATE_EVENT:
                        op = fromOperation + '_' if fromOperation != 'truncate' else ''
                        common.threadLocal.operation = f'{op}truncate_event'
                        self.executeTruncateEvent(path, localId, onedriveId, seqNum, fromOperation)   
                    else:
                        logger.error(f'executeEvents: unknown event {event} for path={path} local_id={localId} onedriveId={onedriveId} seqNum={seqNum}')
                except Exception as e:                    
                    raisedBy = commonfunc.exceptionRaisedBy(e) 
                    if isinstance(e, RetryException):
                        logger.warning(f'executeEvents retry exception: event={event} path={path} path2={path2} local_id={localId} onedriveId={onedriveId} seqNum={seqNum} {raisedBy}')    
                        metrics.counts.incr('eventqueue_retry_exception')                
                    else:                   
                        logger.exception(f'executeEvents exception: event={event} path={path} local_id={localId} onedriveId={onedriveId} seqNum={seqNum} {raisedBy}')
                        metrics.counts.incr('eventqueue_exception')
                    
                        exception = e                        
                finally:
                    if exception == None:
                        db.cache.delete(self.key(event, fromOperation, int(seqNum)), EVENT_PREFIX) 
                        global eventCount
                        eventCount -= 1
                    else:
                        metrics.counts.incr('eventqueue_requeue_event')
                        if isinstance(exception, RetryException):
                            retryCount += 1
                        else:
                            failedCount += 1
                        data = {
                            'path': path,
                            'path2': path2,
                            'local_id': localId,
                            'onedriveId': onedriveId,
                            'failed_count': failedCount,
                            'retry_count': retryCount
                        }
                        db.cache.put(self.key(event, fromOperation, int(seqNum)), bytes(json.dumps(data), 'utf-8'), EVENT_PREFIX)                               
        except Exception as e:
            raisedBy = commonfunc.exceptionRaisedBy(e)
            logger.exception(f'executeEvents: exception {raisedBy}')
            metrics.counts.incr('eventqueue_exception')            
        finally:
            common.threadLocal.operation = saveOperation
            common.threadLocal.path = savePath
    
    def executeDirEvent(self, path: str, localId: str, onedriveId: int, seqNum, fromOperation) -> None:
        metrics.counts.incr(f'execute_{fromOperation}DirEvent')
        logger.info(f'execute: path={path} local_id={localId} onedriveId={onedriveId} seqNum={seqNum}')        
        d = metadata.cache.getattr(path, localId)
        if d != None:
            if d.get('local_only', False):
                logger.warning(f'execute: path={path} is local only, not creating on Google Drive')
            elif gitignore.parser.isIgnored(path):
                logger.info(f'execute: path={path} is gitignored, not creating on Google Drive')
                metadata.cache.changeToLocalOnly(path, localId, d)
            elif d.get('onedrive_id', 0) == 0:               
                logger.info(f'mkdir --> {path} local_id={localId}  onedriveId={onedriveId} mode={oct(d.get("st_mode", 0))}')
                metrics.counts.incr('execute_create_dir')
                mkdir.execute(path, d.get('st_mode', 0), runAsync=False)
                logger.info(f'mkdir <-- {path} local_id={localId} onedriveId={onedriveId}')
            else:
                logger.info(f'dir has onedriveId - noop: {path} local_id={localId}')
        else: 
            if onedriveId == 0: 
                metrics.counts.incr('execute_dir_was_deleted')
                logger.info(f'noop directory was deleted: {path} local_id={localId} onedriveId={onedriveId}')            
            else:
                d = metadata.cache.getattr(path)
                if d != None:
                    logger.info(f'directory still exists locally, not deleting: {path} local_id={localId} onedriveId={onedriveId}')
                    return
                logger.info(f'rmdir --> {path} local_id={localId} onedriveId={onedriveId}')
                metrics.counts.incr('execute_delete_dir')
                tdelete.manager.enqueue(path, localId=localId, onedriveId=onedriveId)
                logger.info(f'rmdir <-- {path} local_id={localId} onedriveId={onedriveId}') 

    def executeFileEvent(self, path: str, localId: str, onedriveId: int, seqNum, fromOperation) -> None:
        metrics.counts.incr(f'execute_{fromOperation}FileEvent')
        logger.info(f'execute: path={path} local_id={localId} onedriveId={onedriveId} seqNum={seqNum}')
        d = metadata.cache.getattr(path, localId)
        if d != None:
            if d.get('local_only', False):
                logger.warning(f'execute: path={path} is local only, not creating on Google Drive')
            elif gitignore.parser.isIgnored(path):
                logger.info(f'execute: path={path} is gitignored, not creating on Google Drive')
                metadata.cache.changeToLocalOnly(path, localId, d)
            elif d.get('onedrive_id', 0) == 0:
                tcreate.manager.enqueue(path, localId)  
                return              
            else:
                logger.info(f'file has onedriveId - noop: {path} local_id={localId} onedriveId={d.get("onedrive_id")}')
            logger.info(f'flush --> {path} local_id={localId} onedriveId={d.get("onedrive_id")}')
            metrics.counts.incr('execute_flush')
            size = tupload.manager.flush(path)
            logger.info(f'flush <-- {path} local_id={localId} onedriveId={d.get("onedrive_id")} size={size}')
        else:
            if onedriveId == 0:
                metrics.counts.incr('execute_file_was_deleted')
                logger.info(f'noop file was deleted or renamed: {path} local_id={localId}')
            else:
                d = metadata.cache.getattr(path)
                if d != None:
                    logger.info(f'file still exists locally, not deleting: {path} local_id={localId} onedriveId={onedriveId}')
                    return
                logger.info(f'delete --> {path} local_id={localId} onedriveId={onedriveId}')
                metrics.counts.incr('execute_delete_file')
                tdelete.manager.enqueue(path, localId=localId, onedriveId=onedriveId)
                logger.info(f'delete <-- {path} local_id={localId} onedriveId={onedriveId}')
    
    
    def executeRenameEvent(self, oldPath: str, newPath:str, localId: str, onedriveId: int, seqNum, fromOperation) -> None:
        metrics.counts.incr(f'execute_{fromOperation}RenameEvent')
        logger.info(f'execute: oldPath={oldPath} newPath={newPath} local_id={localId} onedriveId={onedriveId} seqNum={seqNum}')        
        d = metadata.cache.getattr(newPath, localId)
        if d != None:
            if d.get('local_only', False):
                logger.warning(f'execute: newPath={newPath} is local only, not creating on Google Drive')               
            elif d.get('onedrive_id', 0) == 0:
                if tcreate.manager.pendingCreates.get(localId) != None:                    
                    raise RetryException(f'executeRenameEvent: create pending for local_id={localId}, retrying later')                
                    
                logger.info(f'execute: create non-existing file with new path {newPath} local_id={localId} onedriveId={onedriveId}')
                if d.get('st_mode') & stat.S_IFDIR:
                    self.executeDirEvent(newPath, localId, onedriveId, seqNum, fromOperation)
                elif d.get('st_mode') & stat.S_IFREG == stat.S_IFREG:
                    self.executeFileEvent(newPath, localId, onedriveId, seqNum, fromOperation)
                elif d.get('st_mode') & stat.S_IFLNK == stat.S_IFLNK:
                    self.executeSymlinkEvent(newPath, localId, onedriveId, seqNum, fromOperation)
            else:
                path = attr.getPathFromStat(d)
                if newPath != path:
                    logger.error(f'executeRenameEvent: newPath {newPath} does not match {path} for local_id={localId} onedriveId={onedriveId}')
                    return
                if d.get('onedrive_id') == 0:
                    logger.error(f'executeRe/git/allproxy/.git/objects/pack/tmp_pack_bj1wIqnameEvent: onedriveId is 0 local_id={localId} oldPath={oldPath} newPath={newPath} onedriveId={d.get("onedrive_id")}')
                    return
                logger.info(f'rename --> old={oldPath} new={newPath} local_id={localId}  onedriveId={d.get("onedrive_id")}')
                metrics.counts.incr('execute_rename')
                rename.doRename(oldPath, newPath, d)    
                logger.info(f'rename <-- old={oldPath} new={newPath} local_id={localId}  onedriveId={d.get("onedrive_id")}')        
        else:            
            metrics.counts.incr('execute_rename_was_deleted')
            logger.info(f'noop rename was deleted: old={oldPath} new={newPath} local_id={localId} onedriveId={onedriveId}')
            
    def executeSymlinkEvent(self, path: str, localId: str, onedriveId: int, seqNum, fromOperation) -> None:
        metrics.counts.incr(f'execute_{fromOperation}SymlinkEvent')
        logger.info(f'execute: path={path} local_id={localId} onedriveId={onedriveId} seqNum={seqNum}')
        d = metadata.cache.getattr(path, localId)
        if d != None:
            if d.get('local_only', False):
                logger.warning(f'execute: path={path} is local only, not creating on Google Drive')
            elif gitignore.parser.isIgnored(path):
                logger.info(f'execute: path={path} is gitignored, not creating on Google Drive')
                metadata.cache.changeToLocalOnly(path, localId, d)
            elif d.get('onedrive_id', 0) == 0:
                target = metadata.cache.readlink(path)
                symlink.execute(path, localId, target, runAsync=False)
            else:
                logger.info(f'symlink onedriveId - noop: {path} local_id={localId} onedriveId={d.get("onedrive_id")}')
        else:
            if onedriveId == 0:
                metrics.counts.incr('execute_symlink_was_deleted')
                logger.info(f'noop symlink was deleted or renamed: {path} local_id={localId} onedriveId={onedriveId}')
            else:
                d = metadata.cache.getattr(path)
                if d != None:
                    logger.info(f'symlink still exists locally, not deleting: {path} local_id={localId} onedriveId={d.get("onedrive_id")}')
                    return
                logger.info(f'remove --> {path} local_id={localId} onedriveId={onedriveId}')
                metrics.counts.incr('execute_delete_symlink')
                tdelete.manager.enqueue(path, localId=localId, onedriveId=onedriveId)
                logger.info(f'remove <-- {path} local_id={localId} onedriveId={onedriveId}')

    def executeChmodEvent(self, path: str, localId: str, onedriveId: int, seqNum, fromOperation) -> None:
        metrics.counts.incr(f'execute_{fromOperation}ChmodEvent')
        logger.info(f'execute: path={path} local_id={localId} onedriveId={onedriveId} seqNum={seqNum}')
        d = metadata.cache.getattr(path, localId)
        if d != None:
            if d.get('local_only', False):
                logger.warning(f'execute: path={path} is local only, not changing mode on Google Drive')   
            logger.info(f'chmod --> {path} local_id={localId} onedriveId={onedriveId} mode={oct(d["st_mode"])}')
            metrics.counts.incr('execute_chmod')
            chmod.execute(path, localId, d["st_mode"], d, runAsync=False)
            logger.info(f'chmod <-- {path} local_id={localId} onedriveId={onedriveId}')
        else:
            logger.info(f'chmod onedriveId - noop: {path} local_id={localId} onedriveId={onedriveId}')

    def executeTruncateEvent(self, path: str, localId: str, onedriveId: int, seqNum, fromOperation) -> None:
        metrics.counts.incr(f'execute_{fromOperation}TruncateEvent')
        logger.info(f'execute: path={path} local_id={localId} onedriveId={onedriveId} seqNum={seqNum}')
        d = metadata.cache.getattr(path, localId)
        if d != None:
            if d.get('local_only', False):
                logger.warning(f'execute: path={path} is local only, not truncating on Google Drive')
            elif d.get('onedrive_id', 0) == 0:
                raise RetryException(f'executeTruncateEvent: path={path} local_id={localId} has no onedriveId, retrying later')
            else:
                logger.info(f'truncate --> {path} local_id={localId} onedriveId={onedriveId} size={d.get("st_size")}')
                metrics.counts.incr('execute_truncate')
                truncate.execute(path, localId, d.get("st_size"), d, runAsync=False)
                logger.info(f'truncate <-- {path} local_id={localId} onedriveId={onedriveId}')
        else:
            logger.info(f'truncate onedriveId - noop: {path} local_id={localId} onedriveId={onedriveId}')

queue = EventQueue()