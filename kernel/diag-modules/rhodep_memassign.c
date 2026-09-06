// SPDX-License-Identifier: GPL-2.0
/*
 * rhodep_memassign - hand the memshare region to the modem's VMID over SCM.
 *
 * Diagnostic instrument, and the one piece of the memshare path that cannot
 * live in userspace. The vendor driver, drivers/soc/qcom/memshare/
 * msm_memshare.c, does not just tell the modem an address: before the modem is
 * allowed to touch the buffer it calls
 *
 *	shared_hyp_mapping()
 *	  -> hyp_assign_phys(phys, size, {VMID_HLOS} -> {VMID_MSS_MSA}, RW)
 *
 * which is an SMC into TrustZone that reprogrammes the XPU so the modem
 * subsystem owns those pages. This port answers QMI service 52 from
 * userspace/modem/memshare-daemon.c, which can build the address TLV but has no
 * way to make that call -- there is no ioctl and no sysfs for it. A modem that
 * is handed an address whose pages are still owned by VMID_HLOS and then writes
 * to them takes an XPU violation, which on this SoC is a silent reset, not an
 * error return.
 *
 * So this module is the missing half. It exists as an out-of-tree module rather
 * than a driver because there is no in-tree memshare client to hang it on, and
 * because keeping it loadable means the assignment can be made, measured and
 * undone without a new boot image.
 *
 *	insmod rhodep_memassign.ko                    # defaults: the reserved region
 *	insmod rhodep_memassign.ko size=0x500000      # only what client 1 asks for
 *	insmod rhodep_memassign.ko alloc=1            # allocate 5 MiB here, no dts
 *	rmmod rhodep_memassign                        # gives it back to HLOS
 *
 * The reserved-region defaults match kernel/patches/0120, which reserves
 *
 *	memshare_mem: memshare@8ab00000 {
 *		reg = <0x0 0x8ab00000 0x0 0x800000>;
 *		no-map;
 *	};
 *
 * *** Read this before loading it. *** In its default mode the module refuses
 * unless it can find a no-map reserved-memory node in the live device tree that
 * covers [base, base+size). That guard is not paranoia: assigning memory that
 * Linux still believes it owns hands the modem a live page of the kernel's
 * heap, and the resulting corruption is silent. force=1 removes the guard and
 * there is no good reason to use it.
 *
 * alloc=1 is the way to run this WITHOUT a new boot image. Instead of trusting
 * a reservation, the module takes the memory out of the allocator itself and
 * holds it for its whole lifetime, which makes the guard unnecessary: nothing
 * else can be using pages that dma_alloc_pages() has handed to us and that we
 * never free. It is a 5 MiB (by default) physically contiguous block, which on
 * this kernel has to come from CMA:
 *
 *	CONFIG_ARCH_FORCE_MAX_ORDER=10 -> alloc_pages() tops out at order 10,
 *	4 MiB. Order 11 does not exist here, so the "round 5 MiB up to 8 MiB
 *	with alloc_pages" route is not available. CONFIG_CMA=y, CONFIG_DMA_CMA=y
 *	and a 40 MiB default CMA area are, so dma_alloc_pages() on a bare
 *	platform device lands in dma_alloc_contiguous() -> cma_alloc() and
 *	returns a contiguous block of any size.
 *
 * *** The one thing alloc=1 cannot do. *** CMA pages are ordinary low memory
 * and the kernel's cacheable linear map still covers them after the SCM call
 * moves them to VMID_MSS_MSA; a no-map reservation would not be mapped at all.
 * Two consequences, one handled and one not:
 *
 *   - Dirty lines. dma_direct_alloc_pages() zeroes the buffer through that
 *     cacheable alias, so there are dirty cache lines for these pages at the
 *     moment we hand them over, and a write-back landing after the ownership
 *     change is itself an XPU violation. Handled: the module cleans the range
 *     to the point of coherency immediately before the assignment.
 *   - Speculation. A speculative read through the surviving linear alias could
 *     in principle trip the XPU as well. Not handled, and not handleable from a
 *     module: set_memory_valid() and set_direct_map_invalid_noflush() are not
 *     exported. This is the residual risk of doing it without a dts change, and
 *     it is the reason a reserved no-map region is still the right end state.
 *
 * Load ORDER matters. The assignment must be in place before the modem writes
 * to the buffer, and the modem writes only after it has been given the address,
 * so: load this first, then let the daemon hand out the address. On a boot
 * where the daemon answers the query at ~7 s the practical answer is an
 * ExecStartPre=/sbin/modprobe on rhodep-memshare.service, which is synchronous
 * and therefore a stronger ordering guarantee than modules-load.d.
 *
 * The region is published read-only under /sys/kernel/rhodep_memshare/ so that
 * userspace/modem/memshare-daemon.c can find it without being told:
 *
 *	base		physical base, hex
 *	size		bytes, decimal
 *	assigned	1 once the SCM call has returned 0
 *	dest_vmid	who owns it now
 *	source		"allocated" or "reserved"
 */
#include <linux/module.h>
#include <linux/dma-mapping.h>
#include <linux/kobject.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/platform_device.h>
#include <linux/sizes.h>
#include <linux/sysfs.h>
#include <linux/types.h>
#include <linux/firmware/qcom/qcom_scm.h>
#include <dt-bindings/firmware/qcom,scm.h>

static unsigned long long base = 0x8ab00000ULL;
module_param(base, ullong, 0444);
MODULE_PARM_DESC(base, "physical base of the region to assign (ignored when alloc=1)");

static unsigned long long size;
module_param(size, ullong, 0444);
MODULE_PARM_DESC(size, "size of the region; 0 means 0x500000 with alloc=1, else 0x800000");

static bool alloc;
module_param(alloc, bool, 0444);
MODULE_PARM_DESC(alloc, "allocate the region here instead of using a dts reservation");

static int dma_bits = 32;
module_param(dma_bits, int, 0444);
MODULE_PARM_DESC(dma_bits, "DMA address width for the allocation (32 keeps it below 4 GiB, where stock's region is)");

static int dest_vmid = QCOM_SCM_VMID_MSS_MSA;
module_param(dest_vmid, int, 0444);
MODULE_PARM_DESC(dest_vmid, "destination VMID (0xf = MSS_MSA, the modem)");

static int dest_perm = QCOM_SCM_PERM_RW;
module_param(dest_perm, int, 0444);
MODULE_PARM_DESC(dest_perm, "permission for the destination VMID (6 = RW)");

static int hlos_perm = QCOM_SCM_PERM_RW;
module_param(hlos_perm, int, 0444);
MODULE_PARM_DESC(hlos_perm, "permission to give HLOS back on unload (6 = RW)");

static bool force;
module_param(force, bool, 0444);
MODULE_PARM_DESC(force, "assign even if the region is not reserved (DANGEROUS)");

static bool revert = true;
module_param(revert, bool, 0444);
MODULE_PARM_DESC(revert, "hand the region back to HLOS on unload");

/*
 * qcom_scm_assign_mem() updates this in place: it goes in as the bitmap of the
 * current owners and comes back as the bitmap of the new ones, which is exactly
 * what the reverse call needs.
 */
static u64 srcvm;
static bool assigned;

/* Only set in alloc=1 mode; the buffer is held for the module's lifetime. */
static struct platform_device *alloc_pdev;
static struct page *alloc_pg;
static dma_addr_t alloc_dma;

/*
 * Take the region out of the allocator and keep it. dma_alloc_pages() rather
 * than alloc_pages() because 5 MiB is order 11 and this kernel's MAX_ORDER is
 * 10; dma_alloc_contiguous() reaches CMA, which has no such limit. The device
 * is a bare platform device with no of_node, so it gets dma-direct and the
 * dma_addr_t is the physical address -- checked below rather than assumed.
 */
static int region_allocate(u64 want)
{
	int ret;

	alloc_pdev = platform_device_register_simple("rhodep_memassign", -1,
						     NULL, 0);
	if (IS_ERR(alloc_pdev)) {
		ret = PTR_ERR(alloc_pdev);
		alloc_pdev = NULL;
		pr_err("rhodep_memassign: cannot register the carrier device: %d\n",
		       ret);
		return ret;
	}

	ret = dma_set_mask_and_coherent(&alloc_pdev->dev,
					DMA_BIT_MASK(dma_bits));
	if (ret) {
		pr_err("rhodep_memassign: dma_set_mask_and_coherent(%d) failed: %d\n",
		       dma_bits, ret);
		goto err_dev;
	}

	alloc_pg = dma_alloc_pages(&alloc_pdev->dev, (size_t)want, &alloc_dma,
				   DMA_BIDIRECTIONAL, GFP_KERNEL);
	if (!alloc_pg) {
		pr_err("rhodep_memassign: could not allocate 0x%llx contiguous bytes; CmaFree in /proc/meminfo?\n",
		       want);
		ret = -ENOMEM;
		goto err_dev;
	}

	base = page_to_phys(alloc_pg);
	if ((u64)alloc_dma != base) {
		/*
		 * A translation between the two would mean the address handed
		 * to the modem is not the address TrustZone was asked about.
		 * Refuse rather than guess which one is right.
		 */
		pr_err("rhodep_memassign: dma addr 0x%llx != phys 0x%llx, refusing\n",
		       (u64)alloc_dma, base);
		ret = -EIO;
		goto err_free;
	}

	/*
	 * dma_direct_alloc_pages() zeroed the buffer through the cacheable
	 * linear map, so dirty lines for these pages exist right now. Clean
	 * them to the point of coherency before the pages stop being ours: a
	 * write-back after the assignment is an XPU violation, and on this SoC
	 * that is a silent reset rather than an error.
	 */
	dma_sync_single_for_device(&alloc_pdev->dev, alloc_dma, (size_t)want,
				   DMA_TO_DEVICE);

	pr_info("rhodep_memassign: allocated 0x%llx + 0x%llx (%llu bytes) and holding it\n",
		base, want, want);
	return 0;

err_free:
	dma_free_pages(&alloc_pdev->dev, (size_t)want, alloc_pg, alloc_dma,
		       DMA_BIDIRECTIONAL);
	alloc_pg = NULL;
err_dev:
	platform_device_unregister(alloc_pdev);
	alloc_pdev = NULL;
	return ret;
}

static void region_release(void)
{
	if (alloc_pg) {
		dma_free_pages(&alloc_pdev->dev, (size_t)size, alloc_pg,
			       alloc_dma, DMA_BIDIRECTIONAL);
		alloc_pg = NULL;
		pr_info("rhodep_memassign: 0x%llx + 0x%llx returned to the allocator\n",
			base, size);
	}
	if (alloc_pdev) {
		platform_device_unregister(alloc_pdev);
		alloc_pdev = NULL;
	}
}

/*
 * /sys/kernel/rhodep_memshare/. Read-only, and deliberately not module
 * parameters: the daemon needs a path that does not change if the module is
 * renamed, and "assigned" has to be a fact about the SCM call rather than
 * about what was asked for.
 */
static struct kobject *memshare_kobj;

static ssize_t base_show(struct kobject *k, struct kobj_attribute *a, char *buf)
{
	return sysfs_emit(buf, "0x%llx\n", base);
}

static ssize_t size_show(struct kobject *k, struct kobj_attribute *a, char *buf)
{
	return sysfs_emit(buf, "%llu\n", size);
}

static ssize_t assigned_show(struct kobject *k, struct kobj_attribute *a,
			     char *buf)
{
	return sysfs_emit(buf, "%d\n", assigned ? 1 : 0);
}

static ssize_t dest_vmid_show(struct kobject *k, struct kobj_attribute *a,
			      char *buf)
{
	return sysfs_emit(buf, "0x%x\n", dest_vmid);
}

static ssize_t source_show(struct kobject *k, struct kobj_attribute *a,
			   char *buf)
{
	return sysfs_emit(buf, "%s\n", alloc ? "allocated" : "reserved");
}

static struct kobj_attribute base_attr      = __ATTR_RO(base);
static struct kobj_attribute size_attr      = __ATTR_RO(size);
static struct kobj_attribute assigned_attr  = __ATTR_RO(assigned);
static struct kobj_attribute dest_vmid_attr = __ATTR_RO(dest_vmid);
static struct kobj_attribute source_attr    = __ATTR_RO(source);

static struct attribute *memshare_attrs[] = {
	&base_attr.attr,
	&size_attr.attr,
	&assigned_attr.attr,
	&dest_vmid_attr.attr,
	&source_attr.attr,
	NULL,
};
ATTRIBUTE_GROUPS(memshare);

/*
 * Confirm the region really is reserved and no-map, by walking the live
 * /reserved-memory rather than trusting the caller's numbers. A no-map
 * reservation is the only kind that is safe here: "reusable" or plain reserved
 * memory is still mapped by the kernel.
 */
static bool region_is_reserved(u64 want_base, u64 want_size)
{
	struct device_node *rmem, *child;
	bool found = false;

	rmem = of_find_node_by_path("/reserved-memory");
	if (!rmem) {
		pr_err("rhodep_memassign: no /reserved-memory in the device tree\n");
		return false;
	}

	for_each_child_of_node(rmem, child) {
		struct resource res;
		int i;

		for (i = 0; !of_address_to_resource(child, i, &res); i++) {
			u64 rb = res.start;
			u64 rs = resource_size(&res);

			if (want_base < rb || want_base + want_size > rb + rs)
				continue;
			if (!of_property_read_bool(child, "no-map")) {
				pr_err("rhodep_memassign: %pOFn covers the region but is not no-map\n",
				       child);
				continue;
			}
			pr_info("rhodep_memassign: covered by %pOFn (0x%llx + 0x%llx), no-map\n",
				child, rb, rs);
			found = true;
			break;
		}
		if (found) {
			of_node_put(child);
			break;
		}
	}

	of_node_put(rmem);
	return found;
}

static int __init memassign_init(void)
{
	struct qcom_scm_vmperm dest;
	int ret;

	if (!size)
		size = alloc ? 0x500000ULL : 0x800000ULL;

	if (size & (SZ_4K - 1)) {
		pr_err("rhodep_memassign: size must be non-zero and 4K aligned\n");
		return -EINVAL;
	}

	if (!qcom_scm_is_available()) {
		pr_err("rhodep_memassign: SCM is not available yet\n");
		return -ENODEV;
	}

	if (alloc) {
		ret = region_allocate(size);
		if (ret)
			return ret;
	} else {
		if (base & (SZ_4K - 1)) {
			pr_err("rhodep_memassign: base must be 4K aligned\n");
			return -EINVAL;
		}
		if (!region_is_reserved(base, size)) {
			if (!force) {
				pr_err("rhodep_memassign: 0x%llx + 0x%llx is not a no-map reservation; refusing\n",
				       base, size);
				pr_err("rhodep_memassign: pass alloc=1 to allocate it here, flash a device tree with patch 0120, or pass force=1 and accept the corruption\n");
				return -EPERM;
			}
			pr_warn("rhodep_memassign: region is NOT reserved, proceeding because force=1\n");
		}
	}

	dest.vmid = dest_vmid;
	dest.perm = dest_perm;
	srcvm = BIT(QCOM_SCM_VMID_HLOS);

	ret = qcom_scm_assign_mem((phys_addr_t)base, (size_t)size, &srcvm,
				  &dest, 1);
	pr_info("rhodep_memassign: qcom_scm_assign_mem(0x%llx, 0x%llx, HLOS -> vmid 0x%x perm 0x%x) returned %d\n",
		base, size, dest_vmid, dest_perm, ret);
	if (ret) {
		pr_err("rhodep_memassign: TrustZone refused the transition; the modem must not be given this address\n");
		region_release();
		return ret;
	}

	assigned = true;
	pr_info("rhodep_memassign: 0x%llx + 0x%llx now owned by vmid 0x%x perm 0x%x (srcvm bitmap 0x%llx)\n",
		base, size, dest_vmid, dest_perm, srcvm);

	/*
	 * Published only now, and only on success: the daemon's whole guard is
	 * "assigned == 1", so this must never appear for a region the modem
	 * cannot legally touch.
	 */
	memshare_kobj = kobject_create_and_add("rhodep_memshare", kernel_kobj);
	if (!memshare_kobj) {
		pr_warn("rhodep_memassign: no /sys/kernel/rhodep_memshare; the daemon will not find the region\n");
		return 0;
	}
	if (sysfs_create_groups(memshare_kobj, memshare_groups)) {
		pr_warn("rhodep_memassign: could not create the sysfs attributes\n");
		kobject_put(memshare_kobj);
		memshare_kobj = NULL;
		return 0;
	}
	pr_info("rhodep_memassign: published /sys/kernel/rhodep_memshare (base 0x%llx size %llu)\n",
		base, size);
	return 0;
}

static void __exit memassign_exit(void)
{
	struct qcom_scm_vmperm back;
	int ret;

	if (memshare_kobj) {
		sysfs_remove_groups(memshare_kobj, memshare_groups);
		kobject_put(memshare_kobj);
		memshare_kobj = NULL;
	}

	if (!assigned) {
		region_release();
		return;
	}

	if (!revert) {
		/*
		 * Deliberately leaks the allocation too. Handing pages the
		 * modem still owns back to the buddy allocator is the one
		 * outcome that must never happen.
		 */
		pr_warn("rhodep_memassign: leaving 0x%llx with vmid 0x%x (revert=0); the allocation is leaked on purpose\n",
			base, dest_vmid);
		if (alloc_pdev)
			pr_warn("rhodep_memassign: 0x%llx + 0x%llx will not be reused until reboot\n",
				base, size);
		return;
	}

	back.vmid = QCOM_SCM_VMID_HLOS;
	back.perm = hlos_perm;

	ret = qcom_scm_assign_mem((phys_addr_t)base, (size_t)size, &srcvm,
				  &back, 1);
	if (ret) {
		pr_err("rhodep_memassign: giving 0x%llx back to HLOS failed: %d -- the region is still the modem's\n",
		       base, ret);
		pr_err("rhodep_memassign: NOT freeing it; those pages stay out of the allocator until reboot\n");
		return;
	}

	pr_info("rhodep_memassign: 0x%llx + 0x%llx returned to HLOS\n",
		base, size);
	region_release();
}

module_init(memassign_init);
module_exit(memassign_exit);
MODULE_DESCRIPTION("Assign the memshare region to the modem VMID over SCM (rhodep diagnostic)");
/* qcom_scm_assign_mem is EXPORT_SYMBOL_GPL; this must stay GPL or it will not link. */
MODULE_LICENSE("GPL");
