iCloud Drive FUSE filesystem
============================

This is a first shot at a iCloud Drive FUSE filesystem.

It's depending on pyicloud from https://github.com/picklepete/pyicloud.

netrc support
=============

`~/.netrc` is being read in order to automate the login flow. That way
the plaintext stored passwords can be used rather than having to type
it in all the time. This is naturally insecure.

File format is as follows:
```
machine icloud
login <icloud account login>
password <icloud account password>
```

In addition to login and password you'll need some authentication cookies
if 2 Factor Auth is activated. The fuse driver assumes, that these are around
and has no code to fetch these session cookies.
Use the icloud command line tool from pyicloud to login for now.
If icloud --username <email> --list works, iCloudDriveFuse will work too.

