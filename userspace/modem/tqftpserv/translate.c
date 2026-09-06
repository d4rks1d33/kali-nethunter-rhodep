// SPDX-License-Identifier: BSD-3-Clause
/*
 * Copyright (c) 2019, Linaro Ltd.
 */
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h>
#include <err.h>
#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "translate.h"
#include "zstd-decompress.h"

#define READONLY_PATH	"/readonly/firmware/image/"
#define READWRITE_PATH	"/readwrite/"
#define UPDATES_DIR	"updates/"
#define READONLY_FW_BASE	"/readonly/firmware/"
#define READONLY_MODEM_PATH	READONLY_FW_BASE "modem_pr"
#define READONLY_VENDOR_PATH		"/readonly/vendor/firmware/"
#define READONLY_VENDOR_MNT_PATH	"/readonly/vendor/firmware_mnt/image/"
/*
 * rhodep: catch-all for the rest of /readonly/vendor/. The SM6375 modem asks
 * for /readonly/vendor/fsg/mcfg_{sw,hw}/mbn_{sw,hw}.dig, which on Android is a
 * symlink into the fsg partition (see the AOSP target package
 * rfs_msm_mpss_readonly_vendor_fsg_symlink). Map it under the remoteproc
 * firmware directory, so <fw>/fsg/... serves the fsg partition contents.
 */
#define READONLY_VENDOR_BASE		"/readonly/vendor/"
/*
 * rhodep: the remaining three roots of a Qualcomm RFS namespace. Every root
 * under the stock /vendor/rfs/<x>/<y>/ has the same five entries, and upstream
 * tqftpserv implements only two of them (readonly and readwrite):
 *
 *	hlos      -> /mnt/vendor/persist/hlos_rfs/shared     (shared by all roots)
 *	shared    -> /mnt/vendor/persist/rfs/shared          (shared by all roots)
 *	ramdumps  -> /data/vendor/tombstones/rfs/<subsys>    (per subsystem)
 *	readonly/ -> the firmware and vendor partitions
 *	readwrite -> /mnt/vendor/persist/rfs/<x>/<y>         (per subsystem)
 *
 * All three are named literally in this modem's own firmware -- `strings` on
 * rhodep's modem.mbn yields "/hlos/", "/hlos/qdma/", "/shared/", "/ramdumps/",
 * "/ramdumps/efs_report.txt", "/ramdumps/wlan_minidump" and
 * "/ramdumps/wlan_minidump.count" -- so the modem can and does construct paths
 * under them, and upstream answers "invalid path, rejecting" to all of them.
 */
#define SHARED_PATH	"/shared/"
#define HLOS_PATH	"/hlos/"
#define RAMDUMPS_PATH	"/ramdumps/"

#ifndef ANDROID
#define FIRMWARE_BASE	"/lib/firmware/"
#else
#define FIRMWARE_BASE	"/vendor/firmware/"
#endif

/* rhodep: moved to translate.h, tqftpserv.c needs it too. */
#define TQFTPSERV_RW_DIR	TQFTPSERV_STATE_DIR

/*
 * rhodep: subdirectories of the state directory. Stock keeps `shared` and
 * `hlos` outside the per-subsystem roots because all twelve roots symlink to
 * the same two directories, so they are absolute here and not derived from the
 * currently selected read/write root. `ramdumps` is per subsystem, so it hangs
 * off whatever root is selected.
 *
 * The `rfs_` prefix keeps them clear of every name this modem is known to use
 * under /readwrite/ (cal_rfs, datablock, mot_rfs, ota_firewall, *.bin, *.txt,
 * mcfg.tmp) -- those either have no prefix or end in _rfs.
 */
#define SHARED_DIR	TQFTPSERV_RW_DIR "/rfs_shared"
#define HLOS_DIR	TQFTPSERV_RW_DIR "/rfs_hlos"
#define RAMDUMPS_SUBDIR	"rfs_ramdumps"

static int open_maybe_compressed(const char *path);

/*
 * rhodep: root that /readwrite/ and /ramdumps/ resolve against, switched per
 * QMI service instance by translate_set_rw_root() before each request is
 * dispatched. Defaults to the one root upstream has, so a build that never
 * calls the setter behaves exactly as before.
 */
static const char *rw_root = TQFTPSERV_RW_DIR;

void translate_set_rw_root(const char *root)
{
	rw_root = root ? root : TQFTPSERV_RW_DIR;
}

static void read_fw_path_from_sysfs(char *outbuffer, size_t bufsize)
{
	size_t pathsize;
	FILE *f = fopen("/sys/module/firmware_class/parameters/path", "rt");
	if (!f)
		return;
	pathsize = fread(outbuffer, sizeof(char), bufsize, f);
	fclose(f);
	if (pathsize == 0)
		return;
	/* truncate newline */
	outbuffer[pathsize - 1] = '\0';
}

/**
 * translate_readonly() - open "file" residing with remoteproc firmware
 * @file:	file requested, stripped of "/readonly/image/" prefix
 *
 * It is assumed that the readonly files requested by the client resides under
 * /lib/firmware in the same place as its associated remoteproc firmware.  This
 * function scans through all entries under /sys/class/remoteproc and read the
 * dirname of each "firmware" file in an attempt to find, and open(2), the
 * requested file.
 *
 * As these files are readonly, it's not possible to pass flags to open(2).
 *
 * Return: opened fd on success, -1 otherwise
 */
static int translate_readonly(const char *file)
{
	char firmware_value[PATH_MAX];
	char *firmware_value_copy = NULL;
	char *firmware_path;
	char firmware_attr[32];
	char path[PATH_MAX];
	char fw_sysfs_path[PATH_MAX];
	struct dirent *de;
	int firmware_fd;
	DIR *class_dir;
	int class_fd;
	ssize_t n;
	int fd = -1;

	read_fw_path_from_sysfs(fw_sysfs_path, sizeof(fw_sysfs_path));

	class_fd = open("/sys/class/remoteproc", O_RDONLY | O_DIRECTORY);
	if (class_fd < 0) {
		warn("failed to open remoteproc class");
		return -1;
	}

	class_dir = fdopendir(class_fd);
	if (!class_dir) {
		warn("failed to opendir");
		close(class_fd);
		return -1;
	}

	while ((de = readdir(class_dir)) != NULL) {
		if (!strcmp(de->d_name, ".") || !strcmp(de->d_name, ".."))
			continue;

		if (strlen(de->d_name) + sizeof("/firmware") > sizeof(firmware_attr))
			continue;
		strcpy(firmware_attr, de->d_name);
		strcat(firmware_attr, "/firmware");

		firmware_fd = openat(class_fd, firmware_attr, O_RDONLY);
		if (firmware_fd < 0)
			continue;

		n = read(firmware_fd, firmware_value, sizeof(firmware_value));
		close(firmware_fd);
		if (n < 0) {
			continue;
		}
		firmware_value[n] = '\0';

		firmware_value_copy = strdup(firmware_value);
		firmware_path = dirname(firmware_value_copy);

		/* first try path from sysfs */
		if ((strlen(fw_sysfs_path) > 0) &&
		    (strlen(fw_sysfs_path) + 1 + strlen(firmware_value) + 1 + strlen(file) + 1 < sizeof(path))) {
			strcpy(path, fw_sysfs_path);
			strcat(path, "/");
			strcat(path, firmware_path);
			strcat(path, "/");
			strcat(path, file);

			fd = open_maybe_compressed(path);
			if (fd >= 0)
				break;
			if (errno != ENOENT)
				warn("failed to open %s", path);
		}

		/* now try with base path */
		if (strlen(FIRMWARE_BASE) + strlen(UPDATES_DIR) + strlen(firmware_value) + 1 +
		    strlen(file) + 1 > sizeof(path))
			continue;

		strcpy(path, FIRMWARE_BASE);
		strcat(path, UPDATES_DIR);
		strcat(path, firmware_path);
		strcat(path, "/");
		strcat(path, file);

		fd = open_maybe_compressed(path);
		if (fd < 0) {
			strcpy(path, FIRMWARE_BASE);
			strcat(path, firmware_path);
			strcat(path, "/");
			strcat(path, file);

			fd = open_maybe_compressed(path);
		}
		if (fd >= 0)
			break;

		if (errno != ENOENT)
			warn("failed to open %s", path);
	}

	free(firmware_value_copy);
	closedir(class_dir);

	return fd;
}

/**
 * translate_persistent() - open "file" from a persistent directory
 * @dir:	absolute path of the directory the file is resolved against
 * @file:	relative path of the requested file, with its prefix stripped
 * @flags:	flags to be passed to open(2)
 *
 * rhodep: this is upstream's translate_readwrite(), with the directory made a
 * parameter so the same code can serve /readwrite/, /shared/, /hlos/ and
 * /ramdumps/, and so /readwrite/ can follow the per-instance root.
 *
 * Return: opened fd on success, -1 otherwise
 */
static int translate_persistent(const char *dir, const char *file, int flags)
{
	int base;
	int ret;
	int fd;

	/* Reject directory traversal attempts */
	if (strstr(file, "..")) {
		warn("path traversal attempt rejected: %s", file);
		errno = EACCES;
		return -1;
	}

	ret = mkdir(dir, 0700);
	if (ret < 0 && errno != EEXIST) {
		warn("failed to create %s", dir);
		return -1;
	}

	base = open(dir, O_RDONLY | O_DIRECTORY);
	if (base < 0) {
		warn("failed to open %s", dir);
		return -1;
	}

	fd = openat(base, file, flags, 0600);
	close(base);
	if (fd < 0)
		warn("failed to open %s/%s", dir, file);

	return fd;
}

/**
 * translate_ramdumps() - open "file" under the selected root's ramdumps dir
 * @file:	relative path of the requested file, with /ramdumps/ stripped
 * @flags:	flags to be passed to open(2)
 *
 * rhodep: stock gives each subsystem its own ramdump directory under
 * /data/vendor/tombstones/rfs/. Here it is a subdirectory of the read/write
 * root currently selected, which gives the same one-per-instance separation.
 *
 * Return: opened fd on success, -1 otherwise
 */
static int translate_ramdumps(const char *file, int flags)
{
	char dir[PATH_MAX];
	int ret;

	ret = snprintf(dir, sizeof(dir), "%s/%s", rw_root, RAMDUMPS_SUBDIR);
	if (ret < 0 || (size_t)ret >= sizeof(dir)) {
		errno = ENAMETOOLONG;
		return -1;
	}

	return translate_persistent(dir, file, flags);
}

/**
 * translate_open() - open file after translating path
 *
 * Strips /readonly/firmware/image/ and searches among remoteproc firmware.
 * Strips /readonly/firmware/modem_pr and searches among remoteproc firmware
 * using the modem_pr relative path (e.g., maps to READONLY_FW_BASE/<fw_dir>/modem_pr/...).
 * Strips /readonly/vendor/firmware/ and /readonly/vendor/firmware_mnt/image/
 * and searches among remoteproc firmware.
 * Replaces /readwrite/ with a persistent directory.
 * rhodep: replaces /shared/, /hlos/ and /ramdumps/ with persistent directories
 * too, mirroring the other three entries of a stock RFS root.
 */
int translate_open(const char *path, int flags)
{
	if (!strncmp(path, READONLY_PATH, strlen(READONLY_PATH)))
		return translate_readonly(path + strlen(READONLY_PATH));
	else if (!strncmp(path, READONLY_MODEM_PATH, strlen(READONLY_MODEM_PATH)))
		return translate_readonly(path + strlen(READONLY_FW_BASE));
	else if (!strncmp(path, READWRITE_PATH, strlen(READWRITE_PATH)))
		return translate_persistent(rw_root, path + strlen(READWRITE_PATH), flags);
	else if (!strncmp(path, READONLY_VENDOR_MNT_PATH, strlen(READONLY_VENDOR_MNT_PATH)))
		return translate_readonly(path + strlen(READONLY_VENDOR_MNT_PATH));
	else if (!strncmp(path, READONLY_VENDOR_PATH, strlen(READONLY_VENDOR_PATH)))
		return translate_readonly(path + strlen(READONLY_VENDOR_PATH));
	else if (!strncmp(path, READONLY_VENDOR_BASE, strlen(READONLY_VENDOR_BASE)))
		return translate_readonly(path + strlen(READONLY_VENDOR_BASE));
	else if (!strncmp(path, SHARED_PATH, strlen(SHARED_PATH)))
		return translate_persistent(SHARED_DIR, path + strlen(SHARED_PATH), flags);
	else if (!strncmp(path, HLOS_PATH, strlen(HLOS_PATH)))
		return translate_persistent(HLOS_DIR, path + strlen(HLOS_PATH), flags);
	else if (!strncmp(path, RAMDUMPS_PATH, strlen(RAMDUMPS_PATH)))
		return translate_ramdumps(path + strlen(RAMDUMPS_PATH), flags);

	fprintf(stderr, "invalid path %s, rejecting\n", path);
	errno = ENOENT;
	return -1;
}

/* linux-firmware uses .zst as file extension */
#define ZSTD_EXTENSION ".zst"

/**
 * open_maybe_compressed() - open a file and maybe decompress it if necessary
 * @filename:	path to a file that may be compressed (should not include compression format extension)
 *
 * Return: opened fd on success, -1 on error
 */
static int open_maybe_compressed(const char *path)
{
	char *path_with_zstd_extension = NULL;
	int fd = -1;
	int ret;

	if (access(path, F_OK) == 0)
		return open(path, O_RDONLY);

	ret = asprintf(&path_with_zstd_extension, "%s%s", path, ZSTD_EXTENSION);
	if (ret < 0)
		return ret;

	if (access(path_with_zstd_extension, F_OK) == 0)
		fd = zstd_decompress_file(path_with_zstd_extension);

	free(path_with_zstd_extension);

	return fd;
}
