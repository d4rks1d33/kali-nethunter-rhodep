// SPDX-License-Identifier: GPL-2.0
/*
 * Expose the three Motorola debug carveouts so they can be read after a reset.
 *
 * blair reserves three regions below ramoops, and patch 0067 reserves the same
 * three in this port, so their contents survive a reset with Linux never
 * touching them:
 *
 *   mmi_annotate@aefa1800    2 KiB   Motorola's boot annotation ring
 *   tzlog_dump@aefa2000    192 KiB   backup of TrustZone's log
 *   wdog_cpuctx@aefd2000   184 KiB   per-CPU context saved on a watchdog bite
 *
 * The addresses come from the vendor's include/dt-bindings/moto/
 * moto-mem-reserve.h, where they are expressed relative to RAMOOPS_BASE_ADDR
 * 0xaf000000, and they check out: WDOG_CPUCTX_BASE is 0xaf000000 minus
 * 0x5c00 * 8, which is 0xaefd2000, exactly what this port already reserves.
 *
 * They cannot be read through /dev/mem because CONFIG_STRICT_DEVMEM is set,
 * and a read of that address returns zero bytes with no error at all. Hence
 * this: memremap() the reserved region and hand it out through debugfs.
 *
 * Why bother: the PMIC says these resets are PS_HOLD, meaning the SoC itself
 * asked to be shut down, and the AP is demonstrably alive and silent when it
 * happens. That points at the secure world, and the secure world's log is the
 * one place that might say why. Whether the backup region is actually
 * populated -- by the bootloader from the previous boot, by a downstream
 * kernel that mainline does not have, or not at all -- is exactly what this
 * module is for finding out.
 *
 * Read-only on purpose. Writing to a region the bootloader or TZ owns is not
 * an experiment, it is a way to lose the phone.
 *
 *   make
 *   sudo insmod rhodep_dbgmem.ko
 *   sudo hexdump -C /sys/kernel/debug/rhodep_dbgmem/tzlog | head -40
 *   sudo strings /sys/kernel/debug/rhodep_dbgmem/tzlog | head
 */

#include <linux/debugfs.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/slab.h>

struct region {
	const char *name;
	phys_addr_t base;
	size_t size;
	void *va;
	struct debugfs_blob_wrapper blob;
};

static struct region regions[] = {
	{ .name = "mmi_annotate", .base = 0xaefa1800, .size = 0x800 },
	{ .name = "tzlog",        .base = 0xaefa2000, .size = 0x30000 },
	{ .name = "wdog_cpuctx",  .base = 0xaefd2000, .size = 0x2e000 },
};

static struct dentry *dir;

static void rhodep_dbgmem_release(void)
{
	int i;

	debugfs_remove_recursive(dir);
	dir = NULL;

	for (i = 0; i < ARRAY_SIZE(regions); i++) {
		if (regions[i].va) {
			memunmap(regions[i].va);
			regions[i].va = NULL;
		}
	}
}

static int __init rhodep_dbgmem_init(void)
{
	int i, mapped = 0;

	dir = debugfs_create_dir("rhodep_dbgmem", NULL);
	if (IS_ERR(dir))
		return PTR_ERR(dir);

	for (i = 0; i < ARRAY_SIZE(regions); i++) {
		struct region *r = &regions[i];

		/*
		 * MEMREMAP_WB: these are ordinary DDR that the kernel was told
		 * to leave alone, not device registers. If a region turns out
		 * to be owned by TZ the mapping will still succeed and the
		 * read is what would fail, so map each one separately and keep
		 * going rather than giving up on the first failure.
		 */
		r->va = memremap(r->base, r->size, MEMREMAP_WB);
		if (!r->va) {
			pr_warn("rhodep_dbgmem: could not map %s at %pa\n",
				r->name, &r->base);
			continue;
		}

		r->blob.data = r->va;
		r->blob.size = r->size;
		debugfs_create_blob(r->name, 0400, dir, &r->blob);
		pr_info("rhodep_dbgmem: %s at %pa, %zu bytes\n",
			r->name, &r->base, r->size);
		mapped++;
	}

	if (!mapped) {
		rhodep_dbgmem_release();
		return -ENODEV;
	}

	pr_info("rhodep_dbgmem: %d of %d regions mapped read-only under "
		"/sys/kernel/debug/rhodep_dbgmem\n",
		mapped, (int)ARRAY_SIZE(regions));
	return 0;
}

static void __exit rhodep_dbgmem_exit(void)
{
	rhodep_dbgmem_release();
}

module_init(rhodep_dbgmem_init);
module_exit(rhodep_dbgmem_exit);

MODULE_DESCRIPTION("Read the Motorola debug carveouts that survive a reset");
MODULE_LICENSE("GPL");
