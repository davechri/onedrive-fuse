
import errno
import json
import os
import time
import requests
from fuse import FuseOSError

from onedrive_fuse.remote import token
from onedrive_fuse.log import logger
from onedrive_fuse.config import config
from onedrive_fuse import common

URL_BATCH = 'https://graph.microsoft.com/v1.0/$batch'
URL_ME = '/me/drive'
URL_BASE = 'https://graph.microsoft.com/v1.0/me/drive'

select = '$select=id,name,createdDateTime,lastModifiedDateTime,size,file,folder'

class Api:
    def __init__(self):
        self.heartbeatUrl = f'{URL_BASE}/root/delta?{select}'
        self.token_handler = None
       
    def authenticate(self):   
        client_id = config.getClientId()  
        client_secret = config.getClientSecret()    
        redirect_uri = config.getRedirectUri()
        self.token_handler = token.TokenHandler(
                                     client_id=client_id,
                                     client_secret=client_secret,
                                     redirect_uri=redirect_uri,                                    
                                     scopes=['Files.ReadWrite.All', 'offline_access'])
        if self.token_handler.get_token() == '':
            raise Exception("Failed to authenticate with OneDrive API")

    heartbeatUrl = None
    def heartbeat(self) -> dict[str, any] | None:
        
        verify = 'https_proxy' not in os.environ
        headers = {
            'Authorization': f'Bearer {self.token_handler.get_token()}'
        }  
        response = requests.get(self.heartbeatUrl, headers=headers, verify=verify)        
        if response.status_code == 200:
            j = response.json()
            nextLink = j.get('@odata.nextLink')
            deltaLink = j.get('@odata.deltaLink')
            if deltaLink is not None:
                self.heartbeatUrl = deltaLink
            elif nextLink is not None:
                self.heartbeatUrl = nextLink
        
            return j
        else:
            return None
    
    def mkdir(self, path: str, name: str) -> dict[str, any]:
        
        verify = 'https_proxy' not in os.environ
        headers = {
            'Authorization': f'Bearer {self.token_handler.get_token()}'
        }
        j = {
            "name": name,
            "folder": { },
            "@microsoft.graph.conflictBehavior": "fail"
        }

        retry = 0
        while(retry < 3):
            if path == '/':
                response = requests.post(f'{URL_BASE}/root/children?{select}', headers=headers, json=j, verify=verify)
            else:
                response = requests.post(f'{URL_BASE}/root:{path}:/children?{select}', headers=headers, json=j, verify=verify)
            if response.status_code == 201:
                return response.json()
            elif response.status_code == 409:
                raise FuseOSError(errno.EEXIST)
            elif response.status_code == 400:
                time.sleep(.1)
                retry += 1
                continue
                         
        raise Exception(f"api.post mkdir {path}: {response.status_code}")
    
    def delete(self, path: str) -> None:
        
        verify = 'https_proxy' not in os.environ
        headers = {
            'Authorization': f'Bearer {self.token_handler.get_token()}'
        }
        response = requests.delete(f'{URL_BASE}/root:{path}', headers=headers, verify=verify)
        if response.status_code == 204 or response.status_code == 404:
            return
        raise Exception(f"api.delete failed for {path}: {response.status_code}")
            
    def put(self, path: str, data: bytes) -> dict[str, any]:
        
        verify = 'https_proxy' not in os.environ
        headers = {
            'Authorization': f'Bearer {self.token_handler.get_token()}'
        }

        response = requests.put(f'{URL_BASE}/root:{path}:/content?{select}', headers=headers, data=data, verify=verify)
        if response.status_code in (200,201,204):
            return response.json()
        elif response.status_code == 404:
            raise FuseOSError(errno.ENOENT)
        else:
            raise Exception(f"api.put failed for {path} status={response.status_code}")
        
    def upload(self, path: str, filePath: str) -> None:
        
        verify = 'https_proxy' not in os.environ
        headers = {
            'Authorization': f'Bearer {self.token_handler.get_token()}',
            'Content-Type': 'application/json'
        }
        j = {
            "item": {
                "@odata.type": "microsoft.graph.driveItemUploadableProperties",
                "@microsoft.graph.conflictBehavior": "replace",
                "name": os.path.basename(path)
            }
        }
        size = os.path.getsize(filePath)
        if size <= common.BLOCK_SIZE:
            with open(filePath, 'rb') as f:
                data = f.read()
                self.put(path, data)               
                return
        response = requests.put(f'{URL_BASE}/root:{path}:/createUploadSession', headers=headers, json=j, verify=verify)
        if response.status_code == 200:
            r = response.json()
            uploadUrl = r.get('uploadUrl')
            with open(filePath, 'rb') as f:
                size = os.path.getsize(filePath)
                offset = 0
                while offset < size:
                    block = f.read(common.BLOCK_SIZE)
                    header = {
                        'Content-Length': f'{size}',
                        'Content-Range': f'bytes {offset}-{offset+len(block)-1}/{size}'
                    }
                    response = requests.put(uploadUrl, headers=header, data=block, verify=verify)
                    if response.status_code == 202 or response.status_code == 201 or response.status_code == 200:                        
                        offset += len(block)
                    else:
                        raise Exception(f"Upload failed for {path} status={response.status_code} offset={offset} size={size} blocksize={len(block)}")
                return
        else:
            raise Exception(f"Create upload session failed for {path} status={response.status_code}")
            
    def patch(self, path: str, data: any) -> dict[str, any]:
        
        verify = 'https_proxy' not in os.environ
        headers = {
            'Authorization': f'Bearer {self.token_handler.get_token()}',
            'Content-Type': 'application/json'
        }  
        if path == '/': 
            response = requests.patch(f'{URL_BASE}/root?{select}', headers=headers, json=data, verify=verify)
        else: 
            response = requests.patch(f'{URL_BASE}/root:{path}?{select}', headers=headers, json=data, verify=verify)            
        if response.status_code == 204:
            return response.json()
        elif response.status_code == 404:
            raise FuseOSError(errno.ENOENT)
        raise Exception(f"Patch failed for {path} status={response.status_code} json={data}")
                    
    def readChunk(self, path: str, size: int, offset: int) -> bytes:
        
        verify = 'https_proxy' not in os.environ
        headers = {
            'Authorization': f'Bearer {self.token_handler.get_token()}',
            "Range": "bytes={}-{}".format(offset, offset+size-1)
        }  
        if path == '/': 
            response = requests.get(f'{URL_BASE}/root/content', headers=headers, verify=verify)
        else: 
            response = requests.get(f'{URL_BASE}/root:{path}:/content', headers=headers, verify=verify)
        if response.status_code in (200, 206):
            return response.content
        elif response.status_code == 302:
            url = response.headers.get('location', response.headers.get('Location'))
            response = requests.get(url, headers=headers, verify=verify)
            if response.status_code in (200, 206):
                return response.content
        elif response.status_code == 404:
            raise FuseOSError(errno.ENOENT)
        
        raise Exception(f"readChunk failed for {path} status={response.status_code} size={size} offset={offset}")
    
    def read(self, path: str) -> bytes|None:
        
        verify = 'https_proxy' not in os.environ
        headers = {
            'Authorization': f'Bearer {self.token_handler.get_token()}'
        }  
        if path == '/': 
            response = requests.get(f'{URL_BASE}/root/content', headers=headers, verify=verify)
        else: 
            response = requests.get(f'{URL_BASE}/root:{path}:/content', headers=headers, verify=verify)
        if response.status_code in (200, 206):
            return response.content
        elif response.status_code == 302:
            url = response.headers.get('location', response.headers.get('Location'))
            response = requests.get(url, headers=headers, verify=verify)
            if response.status_code in (200, 206):
                return response.content
        elif response.status_code == 404:
            return None
        
        raise Exception(f"readChunk failed for {path} status={response.status_code}")
    
    # GET https://graph.microsoft.com/v1.0/drives/{drive-id}/root:/{path-relative-to-root}:/children    
    def readdir(self, path: str) -> list[dict[str, any]] | None:
        """
        {
        "createdDateTime": "2023-07-15T00:18:53Z",        
        "id": "89C5D29ABF83F066!674",
        "lastModifiedDateTime": "2023-07-15T00:18:53Z",
        "name": "Desktop",        
        "size": 4703,
        "file": {"mimeType": "application/vnd.microsoft.portable-executable"} 
        "folder": {"childCount": 5}  
        }
        """        
        if path == '/': 
            url1 = f'{URL_ME}/root?{select}'
            url2 = f'{URL_ME}/root/children?{select}'            
        else:
            url1 = f'{URL_ME}/root:{path}?{select}'
            url2 = f'{URL_ME}/root:{path}:/children?{select}'
            
        verify = 'https_proxy' not in os.environ
        headers = {
            'Authorization': f'Bearer {self.token_handler.get_token()}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'      
        }    
       
        body = {
            "requests": [
                {
                    "id": "1",
                    "method": "GET",
                    "url": f'{url1}'
                },
                {
                    "id": "2",
                    "method": "GET",
                    "url": f'{url2}'
                }
            ]
        }
        try:
            logger.debug(f'api.readdir: batch request for {path} url1={url1} url2={url2}')
            response = requests.post(f'{URL_BATCH}', headers=headers, json=body, verify=verify)
            logger.debug(f'api.readdir: batch response for {path} status={response.status_code} response={response.text}')
        except Exception as e:
            logger.error(f"api.readdir failed for {path} exception={e}")
            raise Exception(f"api.readdir failed for {path} exception={e}")
        if response.status_code == 200:
            children: list[dict[str, any]] = []
            r = response.json()
            responses = r.get('responses', [])

            if len(responses) != 2:
                logger.error(f"api.readdir failed for {path} expected 2 responses got {len(responses)} response={response.text}")
                raise Exception(f"api.readdir failed for {path} expected 2 responses got {len(responses)}")

            for resp in responses:
                if resp['id'] == '1':
                    logger.info(f'api.readdir batch response 1: {resp}')
                    if resp['status'] == 404:
                        return None
                    elif resp['status'] != 200:
                        raise Exception(f"readdir failed for {path} {resp}")
                    body = resp.get('body','{}') 
                    found = False           
                    for child in body.get('value', [body]):
                        logger.debug(f'api.readdir batch child: {child}')                 
                        if 'file' in child:
                            child['mimeType'] = child['file'].get('mimeType', 'application/octet-stream')                        
                        else:
                            child['mimeType'] = 'folder'
                        name = os.path.basename(path) if path != '/' else 'root'
                        if child['name'] == name:
                            children.insert(0, child)  # insert the directory itself as the first entry
                            found = True

                    if not found:
                        logger.error(f'api.readdir: directory not found for {path}')
                        return None
                elif resp['id'] == '2':
                    logger.debug(f'api.readdir batch response 2: {resp}')
                    if resp['status'] == 404:
                        return None
                    elif resp['status'] != 200:
                        raise Exception(f"readdir failed for {path} {resp}")
                    body = resp.get('body','{}')
                    for child in body.get('value', [body]):
                        logger.debug(f'api.readdir batch child: {child}')                 
                        if 'file' in child:
                            child['mimeType'] = child['file'].get('mimeType', 'application/octet-stream')                        
                        else:
                            child['mimeType'] = 'folder'
                        children.append(child)
                    logger.debug(f'api.readdir children {path} {children}')                    
                else:
                    logger.error(f"api.readdir failed for {path} unknown response id: {resp}")
                    raise Exception(f"readdir failed for {path} unknown response id: {resp}")
                
            return children
        elif response.status_code == 404:
            return None      

        logger.error(f"api.readdir failed for {path} status={response.status_code} response={response.text}")
        raise Exception(f"readdir failed for {path} status={response.status_code}")

onedrive = Api()