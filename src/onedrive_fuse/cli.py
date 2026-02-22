#!/usr/bin/env python

import getpass
import os
from pathlib import Path
import subprocess

from onedrive_fuse import filesystem
from onedrive_fuse import log
from onedrive_fuse import common
from onedrive_fuse.threads import rpc

CACHE_TIMEOUT = 5 * 60

def main():
    common.threadLocal.operation = 'cli'
    common.threadLocal.path = None
    import argparse
    import argparse
    parser = argparse.ArgumentParser(description='Access remote filesystem using SSH.')    
    subparsers = parser.add_subparsers(dest='operation', help='Available operations')

    # mount
    mount_parser = subparsers.add_parser('mount', help='Mount filesystem')
    mount_parser.add_argument('mountpoint', help='local mount point (eg, ~/mnt)')   
    mount_parser.add_argument('--debug', help='run in debug mode', action='store_true')
    mount_parser.add_argument('--verbose', help='run in verbose debug mode', action='store_true')
    mount_parser.add_argument('--pathfilter', type=str, help='path to filter for logging', default=None)
    mount_parser.add_argument('--updateinterval', type=int, help=f'Update interval in seconds for updating remote filesystem and refreshing cached data (default is {common.UPDATE_INTERVAL})', default=common.UPDATE_INTERVAL)
    mount_parser.add_argument('--clearcache', help='clear the cache', action='store_true')
    mount_parser    
    # unmount
    unmount_parser = subparsers.add_parser('unmount', help='Unmount filesystem')
    unmount_parser.add_argument('mountpoint', help='local mount point (egs, ~/mnt)')

    # rpc operations
    status_parser = subparsers.add_parser('status', help='Dump status every 10 seconds') 
    directories_parser = subparsers.add_parser('directories', help='Dump directories')
    event_parser = subparsers.add_parser('eventqueue', help='Dump event queue')
    metadata_parser = subparsers.add_parser('metadata', help='Dump metadata')    
    unread_parser = subparsers.add_parser('unread', help='Dump unread data blocks')
    
    args = parser.parse_args()
   
    common.dataDir = os.path.join(Path.home(), '.onedrive-fuse')
    if not os.path.exists(common.dataDir):
        os.mkdir(common.dataDir)    

    if args.operation == None:
        parser.print_help()
        exit(1)

    if args.operation == 'unmount':
        rc = subprocess.run(['fusermount', '-u', args.mountpoint]).returncode
        if rc == 0:
            print('unmount successful')
        exit(rc)     

    if args.operation == 'mount':        
        common.debug = args.debug 
        common.verbose = args.verbose 
        common.pathfilter = args.pathfilter
        common.updateinterval = args.updateinterval
        common.mountpoint = args.mountpoint
              
        log.Log().setupConfig(debug=args.debug, verbose=args.verbose) 
        filesystem.FuseOps(args)
    else:
        try:  
            pass     
            if args.operation == 'unread':
                rpc.client.unread()
            elif args.operation == 'directories':
                rpc.client.directories()
            elif args.operation == 'eventqueue':
                rpc.client.eventqueue()
            elif args.operation == 'metadata':
                rpc.client.metadata()
            elif args.operation == 'directories':
                rpc.client.directories()
            elif args.operation == 'status':
                rpc.client.status()
        except Exception as e:
            print('RPC connection failed.')
            print('Make sure that your filesystem is mounted.')   

if __name__ == '__main__':
    main()