
import os
from pathlib import Path
import shutil
import tomllib
from onedrive_fuse.log import logger
from onedrive_fuse import common, commonfunc

class Config:
    def __init__(self):
        pass

    def init(self):
        try:
            configPath = os.path.join(common.dataDir, 'config.toml')
            self.localonlyDir = os.path.join(common.dataDir, 'config.toml')
            if not os.path.exists(configPath):
                defaultConfigPath = Path(__file__).resolve().parent / "default_config.toml"
                shutil.copy(defaultConfigPath, configPath)
                
            with open(configPath, 'rb') as f:
                config = tomllib.load(f)
                logger.debug(f'config: {config}')
                self.localOnlyDirs = config['local_only']  
                self.clientId = config['client_id']
                self.clientSecret = config['client_secret']
                self.redirectUri = config['redirect_uri']         
        except Exception as e:
            self.localOnlyDirs = ['node_modules',]
            raisedBy = commonfunc.exceptionRaisedBy(e)  
            logger.exception(f'Config.__init__: exception loading config: {raisedBy}')            

    def getLocalOnlyDirs(self) -> list[str]:
        return self.localOnlyDirs
    
    def getClientId(self) -> str:
        return self.clientId
    
    def getClientSecret(self) -> str:
        return self.clientSecret
    
    def getRedirectUri(self) -> str:    
        return self.redirectUri
    
config = Config()
