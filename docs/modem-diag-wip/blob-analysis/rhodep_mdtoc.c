// SPDX-License-Identifier: GPL-2.0
/*
 * rhodep_mdtoc - read the Qualcomm MINIDUMP table of contents from SMEM and
 * print, for the modem (minidump id 3), exactly what the kernel's own
 * qcom_minidump() would see at crash time.
 *
 * WHY: on this port the modem PAS descriptor has .minidump_id = 3, so the
 * remoteproc .coredump op is qcom_pas_minidump(), not the plain program-header
 * coredump. qcom_minidump() reads SMEM item 602 (SBL_MINIDUMP_SMEM_ID), indexes
 * subsystems[3], and then:
 *   - if that subsystem is status==1 && enabled=='ENBL' && regions_baseptr!=0,
 *     it builds the dump from the SMEM *minidump region table* (small, curated,
 *     typically data/state only) -> this is the "data-only, no code" result;
 *   - otherwise it FALLS BACK to rproc_coredump() over the program-header
 *     segments registered by qcom_register_dump_segments() at parse_fw ->
 *     which DOES include the code/data load segments (the resident image).
 *
 * This module only READS SMEM (AP-readable, exactly what the kernel does). It
 * touches no XPU-locked modem DDR. It is safe to load on a running system.
 *
 *   make && sudo insmod rhodep_mdtoc.ko
 *   sudo cat /sys/kernel/debug/rhodep_mdtoc/toc
 *   (optional) sudo insmod rhodep_mdtoc.ko ss_id=3 dump_regions=1
 */

#include <linux/debugfs.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/seq_file.h>
#include <linux/soc/qcom/smem.h>

#define SBL_MINIDUMP_SMEM_ID	602
#define QCOM_SMEM_HOST_ANY	(-1)
#define MAX_REGION_NAME_LENGTH	16
#define MINIDUMP_REGION_VALID	('V' << 24 | 'A' << 16 | 'L' << 8 | 'I' << 0)
#define MINIDUMP_SS_ENCR_DONE	('D' << 24 | 'O' << 16 | 'N' << 8 | 'E' << 0)
#define MINIDUMP_SS_ENABLED	('E' << 24 | 'N' << 16 | 'B' << 8 | 'L' << 0)

struct md_region {
	char	name[MAX_REGION_NAME_LENGTH];
	__le32	seq_num;
	__le32	valid;
	__le64	address;
	__le64	size;
};

struct md_subsystem {
	__le32	status;
	__le32	enabled;
	__le32	encryption_status;
	__le32	encryption_required;
	__le32	region_count;
	__le64	regions_baseptr;
};

struct md_global_toc {
	__le32			status;
	__le32			md_revision;
	__le32			enabled;
	struct md_subsystem	subsystems[];
};

static int ss_id = 3;		/* modem = minidump id 3 on sm6375 */
module_param(ss_id, int, 0400);
static bool dump_regions = true;
module_param(dump_regions, bool, 0400);

static struct dentry *dir;

static void fourcc(char out[5], u32 v)
{
	out[0] = (v >> 24) & 0xff;
	out[1] = (v >> 16) & 0xff;
	out[2] = (v >> 8) & 0xff;
	out[3] = v & 0xff;
	out[4] = 0;
}

static int toc_show(struct seq_file *m, void *unused)
{
	struct md_global_toc *toc;
	struct md_subsystem *ss;
	size_t toc_size;
	unsigned int num_ss, i;
	char fc[5];

	toc = qcom_smem_get(QCOM_SMEM_HOST_ANY, SBL_MINIDUMP_SMEM_ID, &toc_size);
	if (IS_ERR(toc)) {
		seq_printf(m, "# SMEM item %d (minidump TOC) not present: %ld\n",
			   SBL_MINIDUMP_SMEM_ID, PTR_ERR(toc));
		seq_puts(m, "# => no minidump TOC: qcom_minidump() returns early,\n");
		seq_puts(m, "#    coredump path degenerates to rproc_coredump()\n");
		seq_puts(m, "#    over the program-header segments (CODE present)\n");
		return 0;
	}

	seq_printf(m, "# minidump global TOC: size=%zu status=%u revision=%u enabled=%u\n",
		   toc_size, le32_to_cpu(toc->status),
		   le32_to_cpu(toc->md_revision), le32_to_cpu(toc->enabled));

	num_ss = (toc_size - offsetof(struct md_global_toc, subsystems)) /
		 sizeof(struct md_subsystem);
	seq_printf(m, "# subsystem slots derivable: %u\n", num_ss);

	if (!toc->status) {
		seq_puts(m, "# TOC status==0 -> kernel treats as 'not found',\n");
		seq_puts(m, "#   qcom_minidump() returns WITHOUT dumping anything.\n");
	}

	if (ss_id < 0 || ss_id >= (int)num_ss) {
		seq_printf(m, "# ss_id %d out of range\n", ss_id);
		return 0;
	}

	ss = &toc->subsystems[ss_id];
	seq_printf(m, "\n# subsystem[%d] (modem):\n", ss_id);
	fourcc(fc, le32_to_cpu(ss->status));
	seq_printf(m, "  status            = %#x\n", le32_to_cpu(ss->status));
	fourcc(fc, le32_to_cpu(ss->enabled));
	seq_printf(m, "  enabled           = %#x ('%s') %s\n",
		   le32_to_cpu(ss->enabled), fc,
		   le32_to_cpu(ss->enabled) == MINIDUMP_SS_ENABLED ? "== ENBL" : "!= ENBL");
	fourcc(fc, le32_to_cpu(ss->encryption_status));
	seq_printf(m, "  encryption_status = %#x ('%s') %s\n",
		   le32_to_cpu(ss->encryption_status), fc,
		   le32_to_cpu(ss->encryption_status) == MINIDUMP_SS_ENCR_DONE ? "== DONE" : "!= DONE");
	seq_printf(m, "  encryption_reqd   = %#x\n", le32_to_cpu(ss->encryption_required));
	seq_printf(m, "  region_count      = %u\n", le32_to_cpu(ss->region_count));
	seq_printf(m, "  regions_baseptr   = %#llx\n",
		   (unsigned long long)le64_to_cpu(ss->regions_baseptr));

	/* Reproduce the kernel's branch decision (qcom_minidump line ~188). */
	if (le64_to_cpu(ss->regions_baseptr) == 0 ||
	    le32_to_cpu(ss->status) != 1 ||
	    le32_to_cpu(ss->enabled) != MINIDUMP_SS_ENABLED) {
		seq_puts(m, "\n# VERDICT: minidump SS not valid/enabled ->\n");
		seq_puts(m, "#   crash coredump FALLS BACK to rproc_coredump()\n");
		seq_puts(m, "#   over program-header segments: CODE+DATA PRESENT.\n");
		seq_puts(m, "#   Arm 'enabled' and force an SSR to capture it.\n");
		return 0;
	}

	if (le32_to_cpu(ss->encryption_status) != MINIDUMP_SS_ENCR_DONE)
		seq_puts(m, "\n# NOTE: encryption_status != DONE -> kernel skips\n"
			    "#   ('Minidump not ready'): no dump at all this crash.\n");
	else
		seq_puts(m, "\n# VERDICT: minidump SS valid+enabled -> crash dump is\n"
			    "#   built from the SMEM region table below (curated,\n"
			    "#   usually DATA/state only -> this is the 'no code' result).\n");

	if (dump_regions && le64_to_cpu(ss->regions_baseptr)) {
		struct md_region __iomem *ptr;
		struct md_region r;
		int seg = le32_to_cpu(ss->region_count);

		ptr = ioremap((unsigned long)le64_to_cpu(ss->regions_baseptr),
			      seg * sizeof(struct md_region));
		if (!ptr) {
			seq_puts(m, "# could not ioremap region table\n");
			return 0;
		}
		seq_puts(m, "\n# region table (name valid addr size):\n");
		for (i = 0; i < (unsigned int)seg; i++) {
			char nm[MAX_REGION_NAME_LENGTH + 1];

			memcpy_fromio(&r, ptr + i, sizeof(r));
			memcpy(nm, r.name, MAX_REGION_NAME_LENGTH);
			nm[MAX_REGION_NAME_LENGTH] = 0;
			fourcc(fc, le32_to_cpu(r.valid));
			seq_printf(m, "  [%2u] %-16s valid=%#x('%s') addr=%#llx size=%#llx\n",
				   i, nm, le32_to_cpu(r.valid), fc,
				   (unsigned long long)le64_to_cpu(r.address),
				   (unsigned long long)le64_to_cpu(r.size));
		}
		iounmap(ptr);
	}
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(toc);

static int __init rhodep_mdtoc_init(void)
{
	dir = debugfs_create_dir("rhodep_mdtoc", NULL);
	if (IS_ERR(dir))
		return PTR_ERR(dir);
	debugfs_create_file("toc", 0400, dir, NULL, &toc_fops);
	pr_info("rhodep_mdtoc: ready (ss_id=%d)\n", ss_id);
	return 0;
}

static void __exit rhodep_mdtoc_exit(void)
{
	debugfs_remove_recursive(dir);
}

module_init(rhodep_mdtoc_init);
module_exit(rhodep_mdtoc_exit);

MODULE_DESCRIPTION("Read the Qualcomm minidump TOC (SMEM 602) for the modem");
MODULE_LICENSE("GPL");
