#!/usr/bin/env python

""" iCloud Drive FUSE Filesystem

Copyright (c) 2020 Andreas Thienemann <andreas@bawue.net>

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.
This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
You should have received a copy of the GNU General Public License along with
this program. If not, see <http://www.gnu.org/licenses/>.
"""

from __future__ import with_statement

import copy
import io
import logging
import netrc
import os
import os.path
import shlex
import stat
import sys
import errno

from cachetools import TTLCache
from fuse import FUSE, FuseOSError, Operations, LoggingMixIn
from pyicloud import PyiCloudService

class ICloudDrive(Operations):
    def __init__(self):
        self.username, _, self.password = netrc.netrc().authenticators("icloud")
        self._api = PyiCloudService(self.username, self.password)
        if self._api.requires_2fa:
            print("Two-factor authentication required.")
            code = input("Enter the code you received of one of your approved devices: ")
            result = self._api.validate_2fa_code(code)
            print("Code validation result: %s" % result)

            if not result:
                print("Failed to verify security code")
                sys.exit(1)
        self.drive = self._api.drive
        self.root = "/"
        self.cache10m = TTLCache(maxsize=10, ttl=600)
        self.uid = os.getuid()
        self.gid = os.getgid()

    def _split_path(self, path):
        """Split a path into all it's elements"""
        parts = []
        while True:
            head, tail = os.path.split(path)
            if head == path:
                parts.insert(0, head)
                break
            elif tail == path:
                parts.insert(0, tail)
                break
            else:
                path = head
                parts.insert(0, tail)
        return parts

    def _path_to_drive(self, path):
        """Get a drive API handle for a path"""
        drive_handle = self._api.drive

        # Handle '/' and just return the base drive_handle
        if path == "/":
            return drive_handle

        logging.debug("Called for %s, gotten %s" % (path, drive_handle))

        try:
            for element in self._split_path(path)[1:]:
                drive_handle = drive_handle[element]
            return drive_handle
        except (KeyError, IndexError):
            raise FuseOSError(errno.ENOENT)

    def getattr(self, path, fh=None):
        dh = self._path_to_drive(path)
        st = {
            "st_uid": self.uid,
            "st_gid": self.gid,
            "st_atime": dh.date_last_open.timestamp() if dh.date_last_open else 0,
            "st_ctime": dh.date_changed.timestamp() if dh.date_changed else 0,
            "st_mtime": dh.date_modified.timestamp() if dh.date_modified else 0,
            "st_nlink": dh.data.get("directChildrenCount", 0) + 2
            if dh.type == "folder"
            else 1,
            "st_size": dh.size if dh.size else 0,
        }
        if dh.type == "folder":
            st.update({"st_mode": stat.S_IFDIR | 0o755})
        elif dh.type == "file":
            st.update({"st_mode": stat.S_IFREG | 0o644})
        return st

    def statfs(self, path):
        """Return a simulation of statvfs(), which gives free blocks and inodes to e.g. df.

        This operation absolutely needs caching as we'd be hammering the server otherwise."""
        try:
            return self.cache10m['statvfs']
        except:
            logging.debug('statvfs cache expired. Fetching fresh...')

            bs = 4096
            st = {
                'f_bsize': 1048576,
                'f_frsize': bs,
                'f_blocks': int(self._api.account.storage.usage.total_storage_in_bytes / bs),
                'f_bfree': int(self._api.account.storage.usage.available_storage_in_bytes / bs),
                'f_files': 0,
                'f_ffree': 0,
                'f_favail': 0,
            }

            self.cache10m['statvfs'] = copy.deepcopy(st)
            return self.cache10m['statvfs']

    def readdir(self, path, fh):
        dh = self._path_to_drive(path)

        dirents = [".", ".."]
        dirents.extend(dh.dir())
        for r in dirents:
            yield r

    def create(self, path, mode):
        """Creates an empty file"""
        head, tail = os.path.split(path)
        dh = self._path_to_drive(head)
        if dh.type == 'folder':
            with io.StringIO('') as file_in:
                file_in.name = tail
                dh.upload(file_in)
        return 0

    def write(self, path, data, offset, fh):
        """Write data to iCloud Drive."""
        logging.debug("write called for %s with size %i and offset %i" % (path, len(data), offset))
        if offset != 0:
            raise NotImplemented
        head, tail = os.path.split(path)
        dh = self._path_to_drive(head)
        if dh.type == 'folder':
            with io.StringIO(data) as file_in:
                file_in.name = tail
                dh.upload(file_in)
        return len(data)


    def unlink(self, path):
        dh = self._path_to_drive(path)
        if dh.type == 'file':
            dh.delete()
        else:
            raise FuseOSError(errno.EACCESS)

    def rmdir(self, path):
        dh = self._path_to_drive(path)
        if dh.type == 'folder':
            dh.delete()
        else:
            raise FuseOSError(errno.ENOTDIR)

    def read(self, path, size, offset, fh):
        logging.debug("read called for %s with size %i and offset %i" % (path, size, offset))
        dh = self._path_to_drive(path)
        headers = {"Range": "bytes=%i-%i" % (offset, size)}
        with dh.open(headers=headers, stream=True) as response:
            return response.raw.read()

    def mkdir(self, path, mode):
        logging.debug("mkdir called for %s" % (path))
        head, tail = os.path.split(path)
        dh = self._path_to_drive(head)
        if dh and dh.type == "folder":
            dh.mkdir(tail)
        else:
            raise FuseOSError(errno.ENOTDIR)

    def rename(self, old, new):
        logging.debug("rename alled for %s -> %s" % (old, new))
        dh = self._path_to_drive(old)
        dh.rename(os.basename(new))

    def rmdir(self, path):
        self.files.pop(path)
        self.files['/']['st_nlink'] -= 1


def main(mountpoint):
    logging.basicConfig(level=logging.DEBUG)
    icloud_drive = ICloudDrive()
    FUSE(icloud_drive, mountpoint, nothreads=True, foreground=True)


if __name__ == "__main__":
    main(sys.argv[1])
