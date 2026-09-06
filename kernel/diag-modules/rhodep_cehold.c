// SPDX-License-Identifier: GPL-2.0
/*
 * rhodep_cehold - hold the RPM crypto-engine clocks enabled.
 *
 * Diagnostic instrument, not a fix. Narrower and louder than rhodep_clkhold:
 * it knows the six clocks by name, so it can print the enable/prepare counts
 * it actually moved instead of leaving that to a separate clk_summary read.
 *
 * The stock vendor tree describes a whole crypto subsystem that mainline
 * sm6375 does not have -- qcedev@1b20000, qcrypto@1b20000, hwkm@4440000,
 * qrng@4453000 -- and every one of those nodes is a consumer of an RPM clock:
 *
 *	clock-names = "core_clk_src", "core_clk", "iface_clk", "bus_clk";
 *	clocks = <&rpmcc 78> x4;		 (CE1)
 *	clock-names = "km_clk_src"; clocks = <&rpmcc 80>; (HWKM)
 *
 * Mainline declares none of them, so nothing on this port ever prepares
 * CE1/HWKM/PKA. clk_smd_rpm_handoff() votes them at probe, and then
 * clk_disable_unused() takes the vote straight back off again at
 * late_initcall_sync, because clk_smd_rpm uses .prepare/.unprepare ops and
 * the prepare count is zero. The question this module exists to ask is
 * whether the modem's GNSS measurement engine reaches a CE slave that the
 * RPM has therefore left unclocked.
 *
 * There is no consumer node to hang a phandle on and no clkdev entry to look
 * up -- clk_smd_rpm registers with devm_clk_hw_register() and an OF provider
 * only, and __clk_lookup() is not exported to modules -- so the clocks are
 * fetched the same way rhodep_clkhold fetches them: straight out of the rpmcc
 * OF provider by index, using the indices from
 * include/dt-bindings/clock/qcom,rpmcc.h.
 *
 *   insmod rhodep_cehold.ko                  # all six (default)
 *   insmod rhodep_cehold.ko sel=ce           # ce1_clk, ce1_a_clk only
 *   insmod rhodep_cehold.ko sel=ce,hwkm      # leave PKA alone
 *   insmod rhodep_cehold.ko sel=all compat="qcom,rpmcc-sm6375"
 *
 * Release without unloading -- and you have to, see below:
 *
 *   echo 0 > /sys/module/rhodep_cehold/parameters/hold    # drop the votes
 *   echo 1 > /sys/module/rhodep_cehold/parameters/hold    # take them again
 *
 * NOTHING CAN BE rmmod'ed ON THIS KERNEL. Not this module, not rhodep_clkhold,
 * not a six-line hello-world built in the same tree:
 *
 *	# insmod hello.ko && rmmod hello
 *	rmmod: ERROR: could not remove 'hello': Device or resource busy
 *
 * with refcnt 0, initstate live, no holders, CONFIG_MODULE_UNLOAD=1 in the
 * headers package and `.exit = cleanup_module` present in the generated
 * .mod.c. delete_module() is taking the `mod->init && !mod->exit` branch, so
 * the running kernel is reading NULL out of a __this_module whose layout its
 * own struct module does not agree with -- the headers package and the running
 * image were configured differently enough to move the .exit member. `rmmod -f`
 * works but skips the exit function entirely, so it LEAKS the clock votes:
 * enable_count goes up and never comes back down.
 *
 * That is why the hold is a writable parameter rather than something tied to
 * module lifetime. Use it. A forced unload of this module permanently pins
 * CE1/HWKM/PKA until the next reboot and silently ruins the baseline for the
 * next measurement.
 */
#include <linux/module.h>
#include <linux/clk.h>
#include <linux/clk-provider.h>
#include <linux/cleanup.h>
#include <linux/dcache.h>
#include <linux/debugfs.h>
#include <linux/fs.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/string.h>

#include <dt-bindings/clock/qcom,rpmcc.h>

#define MAX_CLKS 6

static char *compat = "qcom,rpmcc-sm6375";
module_param(compat, charp, 0444);
MODULE_PARM_DESC(compat, "compatible of the clock provider node");

static char *sel = "all";
module_param(sel, charp, 0444);
MODULE_PARM_DESC(sel, "comma list of groups to hold: all, ce, hwkm, pka");

static const struct cehold_clk {
	const char *name;
	const char *group;
	int idx;
} cehold_clks[MAX_CLKS] = {
	{ "ce1_clk",	"ce",	RPM_SMD_CE1_CLK    },
	{ "ce1_a_clk",	"ce",	RPM_SMD_CE1_A_CLK  },
	{ "hwkm_clk",	"hwkm",	RPM_SMD_HWKM_CLK   },
	{ "hwkm_a_clk",	"hwkm",	RPM_SMD_HWKM_A_CLK },
	{ "pka_clk",	"pka",	RPM_SMD_PKA_CLK    },
	{ "pka_a_clk",	"pka",	RPM_SMD_PKA_A_CLK  },
};

static struct clk *held[MAX_CLKS];
static int nheld;

/*
 * The counts live in struct clk_core, which is private to the framework:
 * __clk_get_enable_count() is not exported, and clk_smd_rpm has no .is_enabled
 * op, so the exported __clk_is_enabled() answers "yes" unconditionally -- which
 * is also why the "hardware enable" column of clk_summary reads Y for these six
 * whether they are voted or not. Do not read that column.
 *
 * So take the numbers from the CCF's own debugfs, which is where clk_summary
 * gets them and is therefore directly comparable with it. Not by opening the
 * file: debugfs_create_u32() files are DEFINE_DEBUGFS_ATTRIBUTE simple
 * attributes with no read_iter, so kernel_read() on one returns -EINVAL and
 * logs "kernel read not supported for file". Walk to the dentry instead and
 * take the u32 that debugfs stashed in the inode's i_private, which for
 * debugfs_create_u32(name, mode, parent, value) is exactly @value, i.e.
 * &core->enable_count.
 *
 * Returns -1 if CONFIG_DEBUG_FS is off or the clock has no debugfs directory,
 * which is informational only and never fatal.
 */
static int cehold_debugfs_count(const char *clk, const char *what)
{
	struct dentry *root, *dir, *file;
	struct inode *inode;
	int val = -1;

	root = debugfs_lookup("clk", NULL);
	if (IS_ERR_OR_NULL(root))
		return -1;

	dir = debugfs_lookup(clk, root);
	if (IS_ERR_OR_NULL(dir))
		goto out_root;

	file = debugfs_lookup(what, dir);
	if (IS_ERR_OR_NULL(file))
		goto out_dir;

	inode = d_inode(file);
	if (inode && inode->i_private)
		val = *(u32 *)inode->i_private;

	dput(file);
out_dir:
	dput(dir);
out_root:
	dput(root);

	return val;
}

static bool cehold_selected(const char *group)
{
	const char *p = sel;
	size_t glen = strlen(group);

	if (!p || !*p)
		return false;

	while (*p) {
		size_t len = strcspn(p, ",");

		if ((len == 3 && !strncmp(p, "all", 3)) ||
		    (len == glen && !strncmp(p, group, glen)))
			return true;

		p += len;
		if (*p == ',')
			p++;
	}

	return false;
}

static DEFINE_MUTEX(cehold_lock);

static void cehold_release(void)
{
	while (nheld--) {
		const char *name = __clk_get_name(held[nheld]);
		int e_before = cehold_debugfs_count(name, "clk_enable_count");
		int p_before = cehold_debugfs_count(name, "clk_prepare_count");

		clk_disable_unprepare(held[nheld]);
		pr_info("rhodep_cehold: %-11s released    enable %d->%d prepare %d->%d\n",
			name, e_before,
			cehold_debugfs_count(name, "clk_enable_count"),
			p_before,
			cehold_debugfs_count(name, "clk_prepare_count"));
		clk_put(held[nheld]);
	}
	nheld = 0;
}

static int cehold_acquire(void)
{
	struct device_node *np;
	int i, ret, nsel = 0;

	np = of_find_compatible_node(NULL, NULL, compat);
	if (!np) {
		pr_err("rhodep_cehold: no node with compatible '%s'\n", compat);
		return -ENODEV;
	}

	for (i = 0; i < MAX_CLKS; i++) {
		const struct cehold_clk *cc = &cehold_clks[i];
		struct of_phandle_args spec = {
			.np = np,
			.args_count = 1,
			.args[0] = cc->idx,
		};
		int e_before, p_before, e_after, p_after;
		struct clk *c;

		if (!cehold_selected(cc->group))
			continue;
		nsel++;

		e_before = cehold_debugfs_count(cc->name, "clk_enable_count");
		p_before = cehold_debugfs_count(cc->name, "clk_prepare_count");

		c = of_clk_get_from_provider(&spec);
		if (IS_ERR(c)) {
			pr_err("rhodep_cehold: %s (idx %d): get failed %ld\n",
			       cc->name, cc->idx, PTR_ERR(c));
			continue;
		}

		if (strcmp(__clk_get_name(c), cc->name))
			pr_warn("rhodep_cehold: idx %d is '%s', expected '%s'\n",
				cc->idx, __clk_get_name(c), cc->name);

		ret = clk_prepare_enable(c);
		if (ret) {
			pr_err("rhodep_cehold: %s (idx %d): enable failed %d\n",
			       cc->name, cc->idx, ret);
			clk_put(c);
			continue;
		}

		e_after = cehold_debugfs_count(cc->name, "clk_enable_count");
		p_after = cehold_debugfs_count(cc->name, "clk_prepare_count");

		held[nheld++] = c;
		pr_info("rhodep_cehold: %-11s idx %3d rate %10lu enable %d->%d prepare %d->%d\n",
			cc->name, cc->idx, clk_get_rate(c),
			e_before, e_after, p_before, p_after);
	}
	of_node_put(np);

	pr_info("rhodep_cehold: %d of %d selected clocks held (sel=\"%s\")\n",
		nheld, nsel, sel);

	if (!nsel) {
		pr_err("rhodep_cehold: sel=\"%s\" selected nothing\n", sel);
		return -EINVAL;
	}

	return nheld ? 0 : -ENODEV;
}

static bool hold = true;

static int cehold_set_hold(const char *val, const struct kernel_param *kp)
{
	bool want;
	int ret;

	ret = kstrtobool(val, &want);
	if (ret)
		return ret;

	guard(mutex)(&cehold_lock);

	if (want == hold)
		return 0;

	if (want) {
		ret = cehold_acquire();
		if (ret)
			return ret;
	} else {
		cehold_release();
	}

	hold = want;
	return 0;
}

static const struct kernel_param_ops cehold_hold_ops = {
	.set = cehold_set_hold,
	.get = param_get_bool,
};
module_param_cb(hold, &cehold_hold_ops, &hold, 0644);
MODULE_PARM_DESC(hold, "1 to hold the votes, 0 to release them (runtime writable)");

static int __init cehold_init(void)
{
	guard(mutex)(&cehold_lock);

	if (!hold) {
		pr_info("rhodep_cehold: loaded idle (hold=0)\n");
		return 0;
	}

	return cehold_acquire();
}

/*
 * Not __exit, and in practice never called: see the header comment. Kept so
 * that the module is correct on a kernel where unloading works, but the
 * supported way to let go is `echo 0 > .../parameters/hold`.
 */
static void cehold_exit(void)
{
	guard(mutex)(&cehold_lock);
	cehold_release();
}

module_init(cehold_init);
module_exit(cehold_exit);
MODULE_DESCRIPTION("Hold the RPM crypto-engine clocks enabled (rhodep diagnostic)");
MODULE_LICENSE("GPL");
