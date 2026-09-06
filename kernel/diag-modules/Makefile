# Out-of-tree diagnostic modules for motorola-rhodep.
#
# These build ON THE PHONE against the installed headers package -- gcc, make
# and /lib/modules/7.2.0-rc5/build are all present there -- which makes them a
# seconds-per-iteration instrument instead of a pmbootstrap build and a flash.
#
#   scp -r kernel/diag-modules kali@phone:/tmp/
#   ssh kali@phone 'cd /tmp/diag-modules && make'
#   sudo insmod rhodep_clkhold.ko idx=8
#
# Build with clang, not gcc. The kernel is built with clang (LLVM=1) and the
# headers package now carries the kernel's real .config, so KBUILD_CFLAGS
# contains clang-only options -- gcc stops at
#   gcc: error: unrecognized command-line option '-fexperimental-late-parse-attributes'
# Only the compiler is clang; GNU binutils still do the `ld -r` module link,
# so lld/llvm-* are not needed on the phone.
#
# The compiler-differs and pahole warnings are expected and harmless: the
# kernel config carries CONFIG_MODULE_ALLOW_BTF_MISMATCH=y precisely so that
# modules built with a different toolchain still load, and
#   Skipping BTF generation for <mod>.ko due to unavailability of vmlinux
# is normal -- the headers package ships no vmlinux, so modfinal skips pahole
# and no BTF is emitted. CONFIG_DEBUG_INFO_BTF_MODULES=y still matters, because
# it is what gives struct module the layout the running kernel expects; see
# README.md, "Why nothing could be rmmod'ed".
obj-m += rhodep_clkhold.o
obj-m += rhodep_cehold.o
obj-m += rhodep_pdhold.o
obj-m += rhodep_reghold.o
obj-m += rhodep_dbgmem.o
obj-m += rhodep_scmtrace.o
obj-m += rhodep_qrtrtrace.o
obj-m += rhodep_kvpeek.o
obj-m += rhodep_memassign.o

KDIR ?= /lib/modules/$(shell uname -r)/build
KCC  ?= clang

all:
	$(MAKE) -C $(KDIR) M=$(PWD) CC=$(KCC) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) CC=$(KCC) clean
