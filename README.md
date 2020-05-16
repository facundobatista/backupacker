# backupacker

A files/dirs packer to make backup on Dropbox

Needs a .yaml with the following information (see [example](backup-example.yaml)): 

- `rootdir`: the root dir to read for everything to do the backup

    e.g.:
        rootdir: /home/johndoe

- `builddir`: the location to build all intermediate structures; if under
  rootdir, it must be ignored later

    e.g.:
        builddir: /home/johndoe/temp/backupbuilder

- `syncdir`: the final place where the intermediate built structures will be synced to

    e.g.:
        syncdir: /home/johndoe/Dropbox/backup

- `ignore_list`: the list of specific paths to ignore, always relative to rootdir; the
  one to be ignored really is the leaf in each case

    e.g.:
        ignore_list:
            - .cache
            - .local/share/Trash
            - Dropbox
            - temp

- `group_levels`: the level on which the grouping must happen; by default it's 0, which
  means that each directory at root level will be packed. If value in 1,
  the directory will be kept, and each dir *inside it* will be packed, etc.

    e.g.:
        group_levels:
            .config: 1
            .local: 2
