import logging
import os
import uuid
import json as jsonlib
import urllib
from urllib.parse import parse_qs
from http.server import SimpleHTTPRequestHandler, HTTPServer
import webbrowser
import requests
from datetime import datetime as dt, timedelta
logger = logging.getLogger(__name__)

from onedrive_fuse import db

REFRESH_TOKEN_KEY = b'refresh_token'
VERIFY = 'https_proxy' not in os.environ

class TokenHandler:

    class TinyAcceptorHTTPServer(HTTPServer):
        """
        When we request an authorisation token from MSFT for our client app we
        provide it with a redirect URI of http://localhost. This is the address the
        browser is redirected to once login/acceptance flow is completed. The actual
        authorisation token is included in the parameters of the URL.

        This small server listens for the above response so it can extract the token
        from the parameter. Either an error is reported (because MSFT sent one back)
        or the auth_code property is set.

        NOTE: This is a very basic, simple HTTP server and, though, it doesn't serve
        any files or local directories it's still an attack surface that could be 
        open for as long as the timeout. 
        
        To mitigate this:

            * The port is random (chosen by the OS)
            * We wait for one request and one request only, then close the server
            * We check state value received in the result matches the one we sent
            with the original authorisation request (see MSFT docs)
            * After a timeout (default 5 minutes) we close the server with an error
        
        Ultimately, up to you whether to use it or not.
        """
        class Handler(SimpleHTTPRequestHandler):
            """
            Handle the GET request from MSFT which will contain our authorisation 
            token in the URL as one of the parameters (or an error!).

            https://learn.microsoft.com/en-us/onedrive/developer/rest-api/getting-started/msa-oauth?view=odsp-graph-online

            Also checks the state code returned matches the one we sent with the 
            original authorisation request.
            
            Sets the authorisation code in the server so it can be retrieved later.
            """
            def do_GET(self):
                data = parse_qs(self.path[2:])
                code = data.get('code','')
                state = data.get('state', '')
                error = data.get('error','')
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                if error != '':
                    print(f'error: got error in MSFT response: {error}.')
                    return ''
                if self.server.get_expected_state() != state[0]:
                    print(f'error: state returned from MSFT does not match state sent in authorisation request (sent: {state[0]}, recvd: {self.server.get_state()}).')
                    return ''
                if code == '':
                    self.wfile.write(bytes('error: didn\'t seem to get authorisation code from MSFT, and no error.', 'utf8'))
                    return ''
                else:
                    self.server.set_auth_code(code[0])
                    self.wfile.write(bytes('Authorised. You can close this browser window, now.', 'utf8'))

        def __init__(self, port=0):
            self._logger = logger.getChild(__class__.__name__)
            super().__init__(server_address=('localhost',port), RequestHandlerClass=self.Handler)
            self._auth_code = ''
            self._state = ''

        def get_port(self):
            return self.server_port

        def get_auth_code(self):
            return self._auth_code
        
        def set_auth_code(self, code):
            self._auth_code = code

        def get_expected_state(self):
            return self._state

        def set_expected_state(self, state):
            self._state = state

        def wait_for_authorisation_code(self, timeout=300):
            """
            We wait for just one request before the server closes. This request 
            should always be MSFT sending either an authorisation code or an 
            error.

            The default timeout is 300 seconds, or five minutes. It's tempting to 
            cut this to 30 seconds, or so, but you need to leave time for the
            user to enter their credentials and accept the scopes when MSFT presents
            them.
            """
            self.timeout = timeout
            with self:
                self._logger.debug(f'listening on ip [{self.server_address}] on port [{self.server_port}]')
                self.handle_request()
            return

        def handle_timeout(self):
            print("error: timeout while waiting for microsoft authorisation code.")
            self._error = f'timeout after {self.timeout} seconds.'
            return ''

    ONEDRIVE_AUTHORISE_URL='https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?'
    ONEDRIVE_TOKEN_SERVER_URL='https://login.live.com/oauth20_token.srf?'

    def _dbg_print_json(self, json_data):
        json_formatted_str = jsonlib.dumps(json_data, indent=2)
        print(json_formatted_str)

    def __init__(self, client_id, client_secret, redirect_uri, scopes=['User.Read']) -> None:
        """
        Handles retrieving tokens from MSFT that can be used to access personal 
        onedrive accounts. Also persists the associated refresh token to a db.

        Args:
            token from storage, does not have to match the name in Azure)
            `client_id` (`string`)  : The app_id/client_id of your registered app 
            taken from the Azure portal
            `scopes` (`[string]`)   : List of scopes required, defaults to 'User.Read' (see [Azure documentation](https://learn.microsoft.com/en-us/graph/permissions-reference))            
            used to store refresh tokens (defaults to './tokens.db')

        Returns:
            `OneDriveTokenHandler`      : A new `OneDriveTokenHandler` object
        """
        self._logger = logger.getChild(__class__.__name__)
        self._scopes = scopes
        if not 'offline_access' in self._scopes:
            self._scopes.append('offline_access')
        self._account = ''
        self._client_id = client_id   
        self._client_secret = client_secret 
        self._redirect_uri = redirect_uri   
        self._port = urllib.parse.urlparse(redirect_uri).port
        self._current_token = ''
        self._current_token_expiry = None      
   
    def _get_refresh_token_from_db(self):
        refresh_token = db.cache.get(REFRESH_TOKEN_KEY, 'token')
        if refresh_token is None:
            self._logger.debug('No refresh_tokens found in token_db, setting empty')
            return ''
        else:
            self._logger.debug(f'Refresh token found in db')            
            return refresh_token

    def _upsert_refresh_token_in_db(self, refresh_token):
        self._logger.debug('Updating refresh token in token_db')
        db.cache.put(REFRESH_TOKEN_KEY, bytes(refresh_token, encoding='utf-8'), 'token')
        
    def _persist_token_data(self, token_data):
        self._current_token = token_data['access_token']
        self._current_token_expiry = dt.now() + timedelta(0,token_data['expires_in'])
        self._logger.debug(f'received token [{self._current_token}] from MSFT with expiry time in [{self._current_token_expiry.strftime("%Y-%m-%d %H:%M:%S")}]')
        refresh_token = token_data['refresh_token']
        self._upsert_refresh_token_in_db(refresh_token=refresh_token)
        self._logger.debug(f'set refresh token [{refresh_token}] in db.')

    def get_token_interactive(self):
        """
        Will open a web-browser at the standard MSFT login page where the user 
        logs in to their MSFT account and accepts the scopes of this
        application. An authorisation code is then redeemed for a token.

        Returns:
            `boolean` : Whether or not a token was retrieved

        """
        http_server = self.TinyAcceptorHTTPServer(port=self._port)
        state = str(uuid.uuid4())
        http_server.set_expected_state(state)
        params = {
                    "client_id": self._client_id,                    
                    "response_type": "code",
                    "redirect_uri": f"http://localhost:{http_server.get_port()}",
                    "response_mode": "query",
                    "scope": ' '.join(self._scopes),
                    "state": state
                }
        self._logger.debug(f'using params to request token: {params}')
        url = self.ONEDRIVE_AUTHORISE_URL + urllib.parse.urlencode(params)
        self._logger.debug(f'opening browser at: [{url}]')
        webbrowser.open(url)
        self._logger.debug(f'starting TinyAcceptorHTTPServer on port [{http_server.get_port()}]')
        http_server.wait_for_authorisation_code(timeout=300)
        if (auth_code := http_server.get_auth_code()) == '':
            self._logger.debug(f'no authorization code received from MSFT.')
            return False
        self._logger.debug(f'authorisation code [{auth_code}] received.')
        params = {
                    "client_id": self._client_id,    
                    "client_secret": self._client_secret,             
                    "redirect_uri": f"http://localhost:{http_server.get_port()}",
                    "code": auth_code,
                    "grant_type": "authorization_code"   
                 }
        self._logger.debug(f'requesting token from [{self.ONEDRIVE_TOKEN_SERVER_URL}]')
        response = requests.post(self.ONEDRIVE_TOKEN_SERVER_URL, headers={'Content-Type': 'application/x-www-form-urlencoded'}, data=urllib.parse.urlencode(params), verify=VERIFY)       
        if 'error' in (json := response.json()):
            print(f'unable to get token, error from get_token_interactive():  {json["error"]} | {json["error_description"]}')
            return False
        self._persist_token_data(json)
        return True
    
    def get_token_refresh(self, refresh_token) -> str:
        """
        Retrieves a new token from Microsoft Graph based on `refresh_token` and
        persists it to the local object and in the database.

        Args:
            `refresh_token` (`string`)   : The refresh token to use.

        Returns:
            `boolean`      : Whether or not a token could be retrieved
        """
        params = {
                    "client_id": self._client_id,    
                    "client_secret": self._client_secret,                                
                    "refresh_token": refresh_token,
                    "scope": ' '.join(self._scopes),
                    "grant_type": "refresh_token"   
                 }
        self._logger.debug(f'requesting token from [{self.ONEDRIVE_TOKEN_SERVER_URL}]')
        params = urllib.parse.urlencode(params)
        self._logger.debug(f'with params: [{params}]')
        response = requests.post(self.ONEDRIVE_TOKEN_SERVER_URL, headers={'Content-Type': 'application/x-www-form-urlencoded'}, data=params, verify=VERIFY)       
        if 'error' in (json := response.json()):
            print(f'unable to get token, error from get_token_refresh():  {json["error"]} | {json["error_description"]}')
            return False
        self._persist_token_data(json)
        return True
    
    def get_token(self):
        """
        Generate a new access token based on the following ordered attempts:
            1: Try and find a current, live token in the cache
            2: Try and find a refresh token in the DB
            3: Get a token using interactive logon

        Returns:
            `string`      : The retrieved token or '' if one could not be found            
        """
        if self._current_token != '' and dt.now() < self._current_token_expiry:
            self._logger.debug('found token in cache, returning.')
            return self._current_token
        self._logger.debug('no valid cached token, checking for refresh token')       
        if (refresh_token := self._get_refresh_token_from_db()) != '':
            self._logger.debug(f"got refresh token [{refresh_token}] from database")
            if self.get_token_refresh(refresh_token):
                return self._current_token
        self._logger.debug('no token in cache or refresh token available, getting interactively.')
        if self.get_token_interactive():
            return self._current_token
        self._logger.debug('unable to get a token from anywhere')
        print('error: unable to get a refresh token by any means, returning empty')
        return ''
