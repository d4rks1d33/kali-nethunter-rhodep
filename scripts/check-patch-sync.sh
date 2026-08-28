#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
#
# Check that the three copies of the patch set agree with each other.
#
# This exists because they silently stopped agreeing. The repository carried
# three copies -- kernel/patches/, kernel/APKBUILD and the aport under
# postmarketos/linux-motorola-rhodep/ -- while the kernel that actually gets
# flashed is built from pmbootstrap's own checkout of the aport. The repository
# copy fell 22 patch files behind without anything complaining, so it described
# a kernel missing six real fixes that were demonstrably running on the phone.
#
# That is worse than an out-of-date file. Reading the stale copy to decide what
# the driver does gives a confident and wrong answer, which is exactly what
# happened while chasing the LTE reset: an IPA memory layout was reasoned about
# from a tree that did not have the patch fixing it.
#
# Run this after changing any patch, and before trusting the repository to
# describe what is on the phone.
#
#   scripts/check-patch-sync.sh

set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
aport="$root/postmarketos/linux-motorola-rhodep"
patches="$root/kernel/patches"
kapk="$root/kernel/APKBUILD"

fail=0
note() { printf '%s\n' "$*"; }
bad() { printf 'FAIL: %s\n' "$*"; fail=$((fail + 1)); }

[ -d "$aport" ] || { bad "no aport at $aport"; exit 1; }

# 1. The APKBUILD in kernel/ must be the same file as the aport's.
if cmp -s "$kapk" "$aport/APKBUILD"; then
	note "ok   kernel/APKBUILD matches the aport's"
else
	bad "kernel/APKBUILD differs from $aport/APKBUILD"
fi

# 2. Every patch named in source= must exist, and its checksum must match.
applied=$(sed -n '/^source=/,/^"/p' "$aport/APKBUILD" |
	grep -oE '[0-9]{4}-[^[:space:]]+\.patch' | sort)
missing=0
for p in $applied; do
	[ -f "$aport/$p" ] || { bad "source= lists $p but it is not in the aport"; missing=$((missing + 1)); }
done
[ "$missing" -eq 0 ] && note "ok   all $(echo "$applied" | wc -l | tr -d ' ') patches in source= are present"

sumbad=0
cd "$aport"
sed -n '/^sha512sums=/,/^"/p' APKBUILD | grep '\.patch' | while read -r sum name; do
	[ -f "$name" ] || continue
	have=$(sha512sum "$name" | cut -d' ' -f1)
	[ "$have" = "$sum" ] || printf 'FAIL: checksum differs for %s\n' "$name"
done > /tmp/.patchsync.$$ || true
if [ -s /tmp/.patchsync.$$ ]; then
	cat /tmp/.patchsync.$$
	sumbad=$(wc -l < /tmp/.patchsync.$$)
	fail=$((fail + sumbad))
else
	note "ok   every sha512sum matches the file it names"
fi
rm -f /tmp/.patchsync.$$

# 3. kernel/patches/ must hold the same files, byte for byte.
drift=0
for f in "$aport"/*.patch; do
	b=$(basename "$f")
	if [ ! -f "$patches/$b" ]; then
		bad "kernel/patches/ is missing $b"
		drift=$((drift + 1))
	elif ! cmp -s "$f" "$patches/$b"; then
		bad "kernel/patches/$b differs from the aport's copy"
		drift=$((drift + 1))
	fi
done
for f in "$patches"/*.patch; do
	b=$(basename "$f")
	[ -f "$aport/$b" ] || { bad "kernel/patches/$b is not in the aport"; drift=$((drift + 1)); }
done
[ "$drift" -eq 0 ] && note "ok   kernel/patches/ and the aport hold the same files"

# 4. Patches that are present but not applied are fine -- diagnostics are kept
#    deliberately -- but they should be visible rather than assumed.
present=$(cd "$aport" && ls *.patch | sort)
unapplied=$(comm -13 <(echo "$applied") <(echo "$present"))
if [ -n "$unapplied" ]; then
	note ""
	note "present but not in source= (kept on purpose, not in the build):"
	echo "$unapplied" | sed 's/^/       /'
fi

note ""
if [ "$fail" -eq 0 ]; then
	note "the three copies agree"
else
	note "$fail problem(s) found"
	exit 1
fi
