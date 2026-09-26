// SPDX-License-Identifier: GPL-2.0
/*
 * rhodep_mpeek - read a page of the modem's physical DDR from the AP, safely.
 *
 * The modem's RF state (the RF-cal-mode gate, the carrier pointer, the FTM
 * session/phase bytes) and the resident RF driver live in DDR owned by
 * VMID_MSS_MSA and protected by the XPU. A plain HLOS ioremap+read of those
 * pages takes an XPU violation, which on this SoC is a silent reset, not an
 * error return. So this module does what the vendor memshare path does: it
 * hyp-assigns the target page to BOTH {HLOS, MSS_MSA} over SCM, ioremaps it
 * non-cached, copies it out, then assigns it back to MSS-only.
 *
 * This is for reading LIVE modem state to answer the RF-cal-mode question (see
 * docs/modem-diag-wip/blob-analysis/dump_targets.md), page by page, NOT an
 * inline coredump (which hung the phone). One page at a time keeps the window
 * where HLOS shares a modem page as small as possible.
 *
 *   make && sudo insmod rhodep_mpeek.ko
 *   echo 0x96f4f740 | sudo tee /sys/kernel/debug/rhodep_mpeek/pa
 *   echo 64        | sudo tee /sys/kernel/debug/rhodep_mpeek/len
 *   sudo cat /sys/kernel/debug/rhodep_mpeek/data     # hexdump of len bytes at pa
 *
 * SAFETY / SCOPE
 *  - Refuses PAs outside the pil-mpss carveout 0x8b800000..0x9b800000 by default
 *    (where VA->PA = VA-0x35000000 lands). Pass allow_any=1 to override for the
 *    separate 0x2a000000 resident pool (RISKIER: not in a known-safe reservation).
 *  - Reads at most one 4K page per request; len is clamped to the page.
 *  - Assigns the page to HLOS+MSS for the read, then back to MSS. If the
 *    assign-back fails it leaves the page shared (does not free it) and says so.
 *  - The modem may be concurrently writing the page; treat data as a snapshot.
 */

#include <linux/debugfs.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/firmware/qcom/qcom_scm.h>
#include <dt-bindings/firmware/qcom,scm.h>

#define PILMSS_BASE  0x8b800000ULL
#define PILMSS_END   0x9b800000ULL

static u64 pa;
static u32 len = 64;
static bool allow_any;
module_param(allow_any, bool, 0644);
MODULE_PARM_DESC(allow_any, "allow PAs outside the pil-mpss carveout (RISKY)");

static struct dentry *dir;

static int do_peek(u64 target, u32 n, void *out)
{
	u64 page = target & ~(PAGE_SIZE - 1);
	u32 off = target & (PAGE_SIZE - 1);
	struct qcom_scm_vmperm share[2], back[1];
	u64 srcvm = BIT(QCOM_SCM_VMID_MSS_MSA);
	void __iomem *io;
	int ret;

	if (n > PAGE_SIZE - off)
		n = PAGE_SIZE - off;
	if (!n)
		return -EINVAL;

	if (!allow_any && (target < PILMSS_BASE || target >= PILMSS_END))
		return -ERANGE;
	if (!qcom_scm_is_available())
		return -ENODEV;

	/* assign the page to HLOS+MSS so HLOS can read it without an XPU fault */
	share[0].vmid = QCOM_SCM_VMID_HLOS;    share[0].perm = QCOM_SCM_PERM_RW;
	share[1].vmid = QCOM_SCM_VMID_MSS_MSA; share[1].perm = QCOM_SCM_PERM_RW;
	ret = qcom_scm_assign_mem((phys_addr_t)page, PAGE_SIZE, &srcvm, share, 2);
	if (ret) {
		pr_err("rhodep_mpeek: assign MSS->{HLOS,MSS} for %#llx failed: %d\n",
		       page, ret);
		return ret;
	}

	io = ioremap_wc(page, PAGE_SIZE);
	if (io) {
		memcpy_fromio(out, io + off, n);
		iounmap(io);
		ret = n;
	} else {
		ret = -ENOMEM;
	}

	/* give the page back to MSS-only */
	back[0].vmid = QCOM_SCM_VMID_MSS_MSA; back[0].perm = QCOM_SCM_PERM_RW;
	/* srcvm now holds the {HLOS,MSS} bitmap from the call above */
	if (qcom_scm_assign_mem((phys_addr_t)page, PAGE_SIZE, &srcvm, back, 1))
		pr_err("rhodep_mpeek: assign back to MSS for %#llx FAILED; page left shared\n",
		       page);
	return ret;
}

static int data_show(struct seq_file *s, void *unused)
{
	u8 *buf;
	int got, i;

	buf = kzalloc(PAGE_SIZE, GFP_KERNEL);
	if (!buf)
		return -ENOMEM;
	got = do_peek(pa, len, buf);
	if (got < 0) {
		seq_printf(s, "peek %#llx failed: %d\n", pa, got);
		kfree(buf);
		return 0;
	}
	seq_printf(s, "pa %#llx len %d:\n", pa, got);
	for (i = 0; i < got; i += 16) {
		int j, m = min(16, got - i);
		seq_printf(s, "%08llx  ", pa + i);
		for (j = 0; j < m; j++)
			seq_printf(s, "%02x ", buf[i + j]);
		seq_puts(s, " |");
		for (j = 0; j < m; j++)
			seq_printf(s, "%c", (buf[i + j] >= 32 && buf[i + j] < 127) ? buf[i + j] : '.');
		seq_puts(s, "|\n");
	}
	kfree(buf);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(data);

static int __init mpeek_init(void)
{
	dir = debugfs_create_dir("rhodep_mpeek", NULL);
	if (IS_ERR_OR_NULL(dir))
		return -ENODEV;
	debugfs_create_x64("pa", 0644, dir, &pa);
	debugfs_create_u32("len", 0644, dir, &len);
	debugfs_create_file("data", 0444, dir, NULL, &data_fops);
	pr_info("rhodep_mpeek: ready. echo a PA to /sys/kernel/debug/rhodep_mpeek/pa, cat data\n");
	return 0;
}

static void __exit mpeek_exit(void)
{
	debugfs_remove_recursive(dir);
}

module_init(mpeek_init);
module_exit(mpeek_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Read a page of modem physical DDR from the AP via SCM assign (rhodep diagnostic)");
