#!/usr/bin/fades

"""Build a backup on Dropbox."""

import argparse
import logging
import os
import pathlib
import shutil
import subprocess
import tarfile
from collections import Counter

import yaml  # fades

logger = logging.getLogger()
logger.setLevel(logging.INFO)
_h = logging.StreamHandler()
_h.setLevel(logging.DEBUG)
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
logger.addHandler(_h)

# useful constants
MB = 1024 ** 2
GB = 1024 ** 3

DROPBOX_FORBIDDEN = {'"', '*', '/', ':', '<', '>', '?', '\\', '|'}


def _encode(char):
    in_hex = hex(ord(char))[2:]
    pq = len(in_hex) // 2
    return '%' * pq + in_hex.upper()


def sanitize(name):
    """Sanitize the name, get something that is really allowed in Dropbox.

    These are:
    - anything contained in DROPBOX_FORBIDDEN
    - `.` (dot) or ` ` (space) as last character of the name
    - anything not in Unicode Basic Multilingual Plane I (i.e. ord() > 2**16)
    """
    final_chars = []
    for char in name:
        if char in DROPBOX_FORBIDDEN or ord(char) > 65535:
            final_chars.append(_encode(char))
        else:
            final_chars.append(char)
    if final_chars[-1] in '. ':
        final_chars[-1] = _encode(final_chars[-1])

    final_name = ''.join(final_chars)
    if final_name != name:
        logger.debug("=== sanitized name %r -> %r", name, final_name)
    return final_name


def _get_name(basename, directory):
    """Get a name based on basename and extension that is not yet in the directory."""
    fpath = pathlib.Path("{}.tar.bz2".format(basename))
    num = 1
    while fpath in directory.iterdir():
        fpath = pathlib.Path("{}-{}.tar.bz2".format(basename, num))
        num += 1
    return directory / fpath


def build_tree(rootdir, builddir, node, to_ignore):
    """Build a whole subtree in a blob."""
    sane_name = sanitize(str(node))
    fname = _get_name(sane_name, builddir)
    tar = tarfile.open(str(fname), 'x:bz2')

    for dirpath, dirnames, filenames in os.walk(rootdir / node):
        if pathlib.Path(dirpath) in to_ignore:
            logger.debug("=== ignoring %r", dirpath)
            dirnames.clear()
            continue

        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            if pathlib.Path(fpath) in to_ignore:
                logger.debug("=== ignoring %r", fpath)
                continue
            if not os.access(fpath, os.R_OK):
                logger.debug("=== skipped unreadable file %r", fpath)
                continue

            relative_path = str(pathlib.Path(fpath).relative_to(rootdir))
            tar.add(fpath, arcname=relative_path)
    tar.close()


def pack_files(rootdir, builddir, all_files):
    """Pack all files for a dir."""
    if not all_files:
        return

    tarfpath = _get_name('_packed_files', builddir)
    tar = tarfile.open(str(tarfpath), 'x:bz2')
    for fname in all_files:
        fpath = str(rootdir / fname)
        if os.access(fpath, os.R_OK):
            tar.add(fpath, arcname=str(fname))
        else:
            logger.debug("=== skipped unreadable file %r", fpath)
    tar.close()


def explore(rootdir, builddir, group_levels, to_ignore, deep=0):
    """Explore structure to build."""
    deepindent = " " * 4 * deep
    relative_levels = {
        str(k.relative_to(rootdir)): v
        for k, v in group_levels.items() if rootdir in k.parents or rootdir == k}
    logger.info("%sExploring %s (levels=%s)", deepindent, rootdir, relative_levels)

    all_files = []
    for node in sorted(rootdir.iterdir()):
        if node in to_ignore:
            logger.debug("=== ignoring %r", str(node))
            continue

        node_relative = node.relative_to(rootdir)
        if not node.is_dir():
            # Not a dir, simple: store to pack later.
            all_files.append(node_relative)
            continue

        if any(group == node or group in node.parents for group in group_levels):
            logger.debug("%s    going down on %s", deepindent, node)
            build_sub = builddir / sanitize(str(node_relative))
            build_sub.mkdir()
            group_levels_sub = {k: v - 1 for k, v in group_levels.items() if v > 1}
            explore(node, build_sub, group_levels_sub, to_ignore, deep=deep + 1)
        else:
            logger.info("%s    building tree for %s in %s", deepindent, node_relative, rootdir)
            build_tree(rootdir, builddir, node_relative, to_ignore)

    logger.info("%s    packing %d files in %s", deepindent, len(all_files), rootdir)
    pack_files(rootdir, builddir, all_files)
    logger.info("%s    --- done", deepindent)


def compare_content(fpath1, fpath2):
    """Tell if the content of both fpaths are equal.

    This does not check modification times, just internal bytes.
    """
    with open(fpath1, 'rb') as fh1:
        with open(fpath2, 'rb') as fh2:
            while True:
                data1 = fh1.read(65536)
                data2 = fh2.read(65536)
                if data1 != data2:
                    return False
                if not data1:
                    return True


def main(config_file):
    """Main entry point."""
    with open(config_file, "rt", encoding="utf8") as fh:
        config = yaml.safe_load(fh)

    # direct info
    rootdir = pathlib.Path(config['rootdir'])
    if not rootdir.root:
        raise ValueError("Bad config! rootdir must be absolute (got {!r})".format(rootdir))
    logger.debug("Root dir: %r", rootdir)
    builddir = pathlib.Path(config['builddir'])
    if not builddir.root:
        raise ValueError("Bad config! builddir must be absolute (got {!r})".format(builddir))
    logger.debug("Build dir: %r", builddir)
    syncdir = pathlib.Path(config['syncdir'])
    if not syncdir.root:
        raise ValueError("Bad config! syncdir must be absolute (got {!r})".format(syncdir))
    logger.debug("Sync dir: %r", syncdir)

    # Load ignore list and verify all relatives.
    to_ignore = set()
    for node_str in config['ignore_list']:
        node_relative = pathlib.Path(node_str)
        node = rootdir / node_relative
        if node_relative.root:
            raise ValueError(
                "Bad config: to-ignore nodes must be relative ({!r} is not)".format(node_str))
        if not node.exists():
            raise ValueError("Bad config: to-ignore node does not exist: {!r}".format(node_str))
        to_ignore.add(node)

    # group levels
    group_levels = {}
    for dirpath_str, value in config['group_levels'].items():
        dirpath_relative = pathlib.Path(dirpath_str)
        dirpath = rootdir / dirpath_relative
        if not dirpath.exists():
            raise ValueError(
                "Bad config: group level node does not exist: {!r}".format(dirpath_str))
        if not dirpath.is_dir():
            raise ValueError(
                "Bad config: group level nodes must be directories ({!r} is not)"
                .format(dirpath_str))
        if len(dirpath_relative.parts) > 1:
            raise ValueError(
                "Bad config: group level can be defined for base dirs only, got {!r}"
                .format(dirpath_str))
        try:
            value = int(value)
        except ValueError as err:
            msg = (
                "Bad config: group level must be a number (got {!r} in path {!r})".format(
                    value, dirpath_str))
            raise ValueError(msg) from err

        group_levels[dirpath] = value

    # Both build and sync dir must NOT be synced! So if they are under root dir, they also need
    # to be in some ignored path
    for checkdir, name in [(builddir, "build"), (syncdir, "sync")]:
        parents = checkdir.parents
        if rootdir in parents:
            if not any(ignored in parents for ignored in to_ignore):
                raise ValueError(
                    "The {} dir is under root dir and not under something ignored.".format(name))

    logger.info("Config validated ok")

    # build!
    if os.path.exists(builddir):
        logger.debug("Removing old build dir")
        shutil.rmtree(builddir)
    os.makedirs(builddir)
    explore(rootdir, builddir, group_levels, to_ignore)

    # stats
    all_stats = []
    for dirpath, dirnames, filenames in os.walk(str(builddir)):
        dirpath = pathlib.Path(dirpath)
        print("========== dirpath", dirpath)
        sync_dirpath = syncdir / dirpath.relative_to(builddir)
        print("========== syncath", sync_dirpath)

        for fname in filenames:
            build_fpath = dirpath / fname
            sync_fpath = sync_dirpath / fname
            print("====== syc", sync_fpath)
            if sync_fpath.exists():
                status = 'equal' if compare_content(build_fpath, sync_fpath) else 'changed'
            else:
                status = 'new'
            size = build_fpath.stat().st_size
            print("=====stat F", build_fpath, size, status)
            all_stats.append((build_fpath.relative_to(builddir), size, status))

    tot_sizes = sum(x[1] for x in all_stats) / GB
    tot_files = len(all_stats)
    discrim = Counter(x[2] for x in all_stats)
    logger.info("Stats: built %d files, total size: %.2f GB", tot_files, tot_sizes)
    logger.info(
        "Stats: %d new, %d changed; showing details for those > 1MB...",
        discrim['new'], discrim['changed'])
    for fpath, size, status in all_stats:
        if size >= MB and status != 'equal':
            logger.info("Stats: %8.2f MB  %-7s  %s", size / MB, status, fpath)

    # copy
    logger.info("Copying to sync destination")
    for node in builddir.iterdir():
        cmd = ['rsync', '-t', '-r', '--delete', '--inplace', str(node), str(syncdir)]
        logger.debug("Running external %s", cmd)
        subprocess.run(cmd, check=True)

    logger.info("Done")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("config", help="Path for config YAML file.")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Loading config from %r", args.config)
    main(args.config)
