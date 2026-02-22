# onedrive-fuse

**onedrive-fuse is a Linux or macOS network filesystem.**

onedrive-fuse is a network filesystem that gives your computer direct access to your
files on a remote server.  onedrive-fuse allows you to use files on your remote server as if they
were files on your local computer.
[text](../onedrive-fuse/README.md)
onedrive-fuse performs an on-demand download of files when your computer
attempts to use them.  

## Key features

onedrive-fuse has several nice features:

- **Only download files when you use them.** The deafult is to only
  download a file if you (or a program on your computer) uses that file. 

- **Bidirectional sync.** onedrive-fuse will only download a file when you access a file
  that has been changed remotely. If you somehow simultaneously
  modify a file both locally on your computer and also remotely on your server,
  onedrive-fuse will keep the most recently modified file.  The default is to check your remote server once every minute for any remotely changed files.

- **Can be used offline.** Files you've opened previously will be available even
  if your computer has no access to the internet.  While your computer is disconnected
  from the internet, you can continue to update the local filesystem and when you
  reconnect to the internet, the locally updated files will be uploaded to your server. 
    
- **Fast.** onedrive-fuse asynchronously performs network requests to your server once every 10 seconds. onedrive-fuse caches both filesystem metadata and file contents both in memory and on-disk.  Filesytem operations will always be fast unless a large file must be read from Google Drive.

- **Symbolic Links.** onedrive-fuse supports symbolic links (e.g., ln -s command).

- **gitignore files.** Files that match a `.gitignore` file are only cached locally and are
  not uploaded to your server.  This means that Python virtual environment packages in `.venv`
  and nodejs packages in `node_modules` are only installed locally.  
  
 **Local Only directories.** Directories can be configued to only store files locally in your native filesystem to improve small file performance.  The `~/.onedrive-fuse/config.toml` file is used to configure local only directories.  The`node_modules` directory is configured as a local only directory so node packages are stored locally in your native filesystem, and `npm install` will run very fast.

 This is the default `config.toml' file:
 ```json
### This is the configuration file.
### It should be placed in $HOME/.onedrive-fuse/config.toml

# Directory names that are only created localy and not synced to Google Drive.
# Files in local only directories are stored in the native filessystem for fast
# updates.  Install directories are good candidates to be stored locally.
local_only = [
    "node_modules",    
]
 ```
 
## Quick start

### Install FUSE

#### OSX

On Mac OSX, onedrive-fuse requires [osxfuse](https://osxfuse.github.io/) and [pkg-config](http://macappstore.org/pkg-config/):

```bash
$ brew update; brew install pkg-config; brew tap homebrew/cask; brew install --cask osxfuse
```

#### Ubuntu / Debian

On Ubuntu / Debian, onedrive-fuse requires [libfuse-dev](https://packages.ubuntu.com/disco/libfuse-dev), [libssl-dev](https://packages.ubuntu.com/disco/libssl-dev) and [pkg-config](https://packages.ubuntu.com/disco/pkg-config):

```bash
sudo apt-get install -y fuse3 libfuse-dev libssl-dev pkg-config
```

#### Fedora

On Fedora, onedrive-fuse requires gcc, fuse3-devel, and pkg-config:

```bash
sudo dnf install -y gcc fuse3-devel pkg-config
```

### Authentication
These authentication parameters must be configured in your **~/.onedrive-fuse/config.toml** file:
* client_id
* client_secret
* redirect_url

Here is an example of how to configure authentication tokens in the **~/.onedrive-fuse/config.toml** file:
```sh
# Registrater a onedrive application:
# 1. Go to: https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBladeh
# 2. Click "+ New registration"
# 3. Enter a name: "OneDrive FUSE"
# 4. Set supported account types to "Personal accounts only"
# 5. Select platform: "Web"
# 6. Set the redirect URI to "http://localhost:8080"
# 7. Create client secret.
client_id = "copy client_secret here"
client_secret = "copy client_secret here"
redirect_uri = "http://localhost:8080"
```

### Install onedrive-fuse:

### MacOS

Install pipx:
```sh
brew install pipx
pipx ensurepath
sudo pipx ensurepath --global # optional to allow pipx actions with --global argument
pipx install onedrive-fuse
```

#### Ubuntu / Debian

```sh
sudo apt update
sudo apt install pipx
pipx ensurepath
sudo pipx ensurepath --global # optional to allow pipx actions with --global argument
pipx install onedrive-fuse
```

#### Fedora

```sh
sudo dnf install pipx
pipx ensurepath
sudo pipx ensurepath --global # optional to allow pipx actions with --global argument
pipx install onedrive-fuse
```

### Mount Filesystem

Create mountpoint directory:
```sh
MOUNTPOINT=~/mnt
mkdir $MOUNTPOINT
```

Mount Filesystem:
```sh
onedrive-fuse mount $MOUNTPOINT # add --downloadall to sync all files
```

Notes:
- It's recommended to run it as user, not as root.  For this to work the mountpoint must be owned by the user.
- The cache update interval defaults to 1 minutes, and can be set with the --updateinterval option.

### Unmount Filesystem

```sh
onedrive-fuse unmount $MOUNTPOINT
```
or
```sh
fusermount -u $MOUNTPOINT
```

### Display onedrive-fuse activity:

```sh
onedrive-fuse status
```

## Building onedrive-fuse yourself

Create Virtual Environment:

```sh
cd onedrive-fuse
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Mount filesystem:

```sh
$ ./src/onedrive_fuse/cli.py $MOUNTPOINT

```

### Running tests

The best way to test is to clone a repo, and install, build and run the application.

Mount Filesystem:
```sh
onedrive-fuse mount $MOUNTPOINT
```

Build and run web application:
```bash
cd $MOUNTPOINT
git clone https://github.com/allproxy/allproxy.git
npm install
npm run build
npm start
```

Run python application:
```bash
cd $MOUNTPOINT
git clone https://github.com/davechri/onedrive-fuse.git
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
./src/onedrive_fuse/cli.py status
```

## Troubleshooting

If the filesystem appears to hang, its possible the fileystem has crashed. To resolve this:
- **Remount:** `fusermount -u $MOUNTPOINT` and remount `onedrive-fuse mount $MOUNTPOINT
- **Clear the local cache:** `fusermount -u $MOUNTPOINT` and remount `onedrive-fuse mount $MOUNTPOINT --clearcache`
- **Restart your computer**

### Logging

- **Error Log:** error logs are logged to `~/.onedrive-fuse/error.log`

#### Debug Logging

For debug logging use the --debug option.  The --verbose option will produce more verbose logging.
```sh
onedrive-fuse mount $MOUNTPOINT --debug &> ~/path_to_my_log_file
```

### Display onedrive-fuse status

Filesystem statistics are displayed every 10 seconds.

```sh
onedrive-fuse status
```
```sh
08:28:45 ONLINE:
	LOCAL_ONLY:   /home/dave/.onedrive-fuse/home/localonly=1.0 GB
	REMOTE_FILES: dirs=526   files=934      links=17   cached=13.1 MB   fileBytes=1.1 GB
08:28:57 ONLINE:
	LOCAL_ONLY:   /home/dave/.onedrive-fuse/home/localonly=1.0 GB
	REMOTE_FILES: dirs=526   files=934      links=17   cached=13.1 MB   fileBytes=1.1 GB
08:29:09 ONLINE:
	REMOTE CALLS: readdir=1 
	LOCAL_ONLY:   /home/dave/.onedrive-fuse/home/localonly=1.0 GB
	REMOTE_FILES: dirs=564   files=941      links=20   cached=13.1 MB   fileBytes=1.1 GB
```

### Dump Internal State

Display metadata:
```sh
onedrive-fuse metadata
```

Display directory entries:
```sh
onedrive-fuse directories
```

Diaplay all unread files:
```sh
onedrive-fuse unread
```

Display the event queue:
```sh
onedrive-fuse eventqueue
```



