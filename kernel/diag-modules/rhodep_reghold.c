// SPDX-License-Identifier: GPL-2.0
/*
 * rhodep_reghold - hold one regulator enabled, at a voltage, with a load.
 *
 * Diagnostic instrument, not a fix. Same reason rhodep_clkhold, rhodep_cehold
 * and rhodep_pdhold exist: the question is "does the modem need a rail the
 * application processor never votes for?", and the rail in question has no
 * consumer node anywhere in mainline's device tree, so there is no phandle to
 * hang a supply on without a new DTB and a new boot image.
 *
 * The rail this was written for is pmr735a_l1. The stock tree holds it and
 * mainline does not:
 *
 *	rpm-regulator-ldoe1 {			// RPM resource "ldoe" id 1
 *		qcom,hpm-min-load = <0x2710>;	// 10 mA
 *		proxy-supply = <&pmr735a_l1>;
 *		regulator-l1 {
 *			regulator-name = "pmr735a_l1";
 *			regulator-min-microvolt = <570000>;
 *			regulator-max-microvolt = <650000>;
 *			qcom,init-voltage = <600000>;
 *			qcom,proxy-consumer-enable;
 *			qcom,proxy-consumer-current = <62000>;
 *		};
 *	};
 *
 * Mainline declares the same rail in the rhodep dts and nothing references it,
 * so it reads  l1  0  0  0  unknown  576mV  0mA  in regulator_summary: zero
 * users, zero open, off, and parked at the bottom of its declared range. It is
 * the only proxy-enabled rail in the whole stock tree with no AP-side consumer
 * node, and 0.6 V with a 62 mA proxy is an RF/PLL supply, not a logic supply.
 *
 *   insmod rhodep_reghold.ko name=l1                # 600000 uV, 62000 uA
 *   insmod rhodep_reghold.ko name=l1 uv=650000
 *   insmod rhodep_reghold.ko name=l1 hold=0         # report only, vote nothing
 *
 * Release without unloading -- and you have to, see below:
 *
 *   echo 0 > /sys/module/rhodep_reghold/parameters/hold
 *   echo 1 > /sys/module/rhodep_reghold/parameters/hold
 *
 * ---------------------------------------------------------------------------
 * HOW IT GETS A REGULATOR THAT HAS NO CONSUMER NODE
 *
 * It does not need one, and that is worth writing down because the obvious
 * reading of the regulator API says otherwise. regulator_get() resolves a
 * supply in three steps (drivers/regulator/core.c, regulator_dev_lookup()):
 *
 *	1. regulator_dt_lookup()  - "<id>-supply" in the consumer's own OF node
 *	2. regulator_map_list     - board-file style consumer_supplies
 *	3. regulator_lookup_by_name(id)   <-- this one
 *
 * The third is a plain class_find_device() over the regulator class comparing
 * rdev_get_name(), which is constraints->name ("regulator-name" in DT) or,
 * when that is absent, desc->name. Mainline's rhodep dts gives the pmr735a
 * rails no regulator-name, so the name is the one in qcom_smd-regulator.c's
 * rpm_pmr735a_regulators[] table: "l1". Passing dev == NULL skips step 1
 * entirely and step 2 matches nothing, so a bare
 *
 *	regulator_get_optional(NULL, "l1")
 *
 * lands on pmr735a_l1 with no device tree change at all. Verified on the
 * device: open count went 0 -> 1 and the handle read back 576000 uV, disabled.
 *
 * _optional_, not the plain get: on a platform with full constraints
 * regulator_get() answers a failed lookup with dummy_regulator_rdev and only a
 * dev_warn, so a typo would silently return a handle that enables nothing and
 * reads back as success. regulator_get_optional() returns -ENODEV instead.
 *
 * The names are the PMIC driver's, so they are NOT unique across PMICs: this
 * board has a pm6125 and a pmr735a and both have an l2..l7. class_find_device()
 * takes the first match and there is no way to say which from here. "l1" is
 * unambiguous (mainline declares no pm6125_l1), which is why this works for the
 * rail it was written for; for anything else, count the hits first:
 *
 *   for d in /sys/class/regulator/regulator.[0-9]*; do cat $d/name; done \
 *	| sort | uniq -c
 *
 * and rely on the guard_lo/guard_hi window as the backstop.
 * ---------------------------------------------------------------------------
 * NOTHING CAN BE rmmod'ed ON THIS KERNEL, and the exact reason is known.
 *
 *	# insmod hello.ko && rmmod hello
 *	rmmod: ERROR: could not remove 'hello': Device or resource busy
 *
 * refcnt 0, initstate live, no holders, CONFIG_MODULE_UNLOAD=y, and
 * `.exit = cleanup_module` present in the generated .mod.c with a good
 * R_AARCH64_ABS64 relocation into .gnu.linkonce.this_module. delete_module()
 * takes its `mod->init && !mod->exit` branch because the running kernel reads
 * mod->exit from a different offset than the one the module was built for:
 *
 *	CONFIG_DEBUG_INFO_BTF_MODULES=y   in the running image (/proc/config.gz)
 *	CONFIG_DEBUG_INFO_BTF_MODULES     unset in the installed headers package
 *
 * That option adds four members to struct module --
 *
 *	unsigned int btf_data_size, btf_base_data_size;
 *	void *btf_data, *btf_base_data;			// 24 bytes
 *
 * -- and they sit *between* .init and .exit (include/linux/module.h: the BTF
 * block is after CONFIG_BPF_EVENTS and before CONFIG_JUMP_LABEL, and .exit is
 * further down under CONFIG_MODULE_UNLOAD). So .init lands at the right offset
 * and modules load; .exit, .refcnt and .jump_entries are all read 24 bytes low
 * and mod->exit comes out NULL. Marking the exit function __exit or not makes
 * no difference. The fix is a headers package built from the running image's
 * config, not a change here.
 *
 * `rmmod -f` works but SKIPS the exit function, so it would leak the enable and
 * the load and leave this rail voted until the next reboot. That is why the
 * hold is a writable parameter rather than something tied to module lifetime.
 * Use it.
 * ---------------------------------------------------------------------------
 *
 * Deliberately one rail and not an idx= array like its siblings: a voltage and
 * a load only mean anything against a particular rail, and a shared uv=
 * applied to a list of them is a way to put 600 mV on something that wanted
 * 3.3 V.
 */
#include <linux/cleanup.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/regulator/consumer.h>

static char *name = "l1";
module_param(name, charp, 0444);
MODULE_PARM_DESC(name, "regulator name, as in /sys/class/regulator/*/name");

static int uv = 600000;
module_param(uv, int, 0444);
MODULE_PARM_DESC(uv, "voltage to request in uV (stock qcom,init-voltage)");

static int uv_max;
module_param(uv_max, int, 0444);
MODULE_PARM_DESC(uv_max, "upper bound of the request in uV (0 = same as uv)");

static int ua = 62000;
module_param(ua, int, 0444);
MODULE_PARM_DESC(ua, "load to declare in uA (stock qcom,proxy-consumer-current)");

/*
 * Refuse to drive a rail whose present voltage is nowhere near the one asked
 * for. Not a precision check: it is the backstop for the name collision
 * described above, so that "l2" cannot put 600 mV on a 3.3 V rail because
 * class_find_device() happened to return the pm6125 one.
 */
static int guard_lo = 400000;
module_param(guard_lo, int, 0444);
MODULE_PARM_DESC(guard_lo, "refuse the rail if it reads below this, in uV");

static int guard_hi = 900000;
module_param(guard_hi, int, 0444);
MODULE_PARM_DESC(guard_hi, "refuse the rail if it reads above this, in uV");

static DEFINE_MUTEX(reghold_lock);
static struct regulator *reg;
static bool enabled_by_us;

static void reghold_report(const char *when)
{
	pr_info("rhodep_reghold: %-8s %s: enabled=%d voltage=%d uV\n",
		when, name, regulator_is_enabled(reg), regulator_get_voltage(reg));
}

/* Take the consumer handle, once, and sanity-check the rail. Caller locks. */
static int reghold_attach(void)
{
	int now;

	if (reg)
		return 0;

	reg = regulator_get_optional(NULL, name);
	if (IS_ERR(reg)) {
		pr_err("rhodep_reghold: '%s': get failed %ld (no such regulator?)\n",
		       name, PTR_ERR(reg));
		reg = NULL;
		return -ENODEV;
	}

	now = regulator_get_voltage(reg);
	if (now < 0) {
		pr_err("rhodep_reghold: '%s': get_voltage %d\n", name, now);
		goto err_put;
	}
	if (now < guard_lo || now > guard_hi) {
		pr_err("rhodep_reghold: '%s' reads %d uV, outside the guard "
		       "%d..%d uV -- refusing. Wrong rail? Widen it with "
		       "guard_lo=/guard_hi= if this is deliberate.\n",
		       name, now, guard_lo, guard_hi);
		goto err_put;
	}

	reghold_report("attached");
	return 0;

err_put:
	regulator_put(reg);
	reg = NULL;
	return -ERANGE;
}

/* Caller locks. */
static int reghold_acquire(void)
{
	int ret;

	ret = reghold_attach();
	if (ret)
		return ret;

	if (enabled_by_us)
		return 0;

	ret = regulator_set_voltage(reg, uv, uv_max);
	if (ret)
		pr_err("rhodep_reghold: '%s': set_voltage %d..%d uV failed %d\n",
		       name, uv, uv_max, ret);

	ret = regulator_enable(reg);
	if (ret) {
		pr_err("rhodep_reghold: '%s': enable failed %d\n", name, ret);
		return ret;
	}
	enabled_by_us = true;

	/*
	 * After the enable, not before: regulator_set_load() only reaches
	 * drms_uA_update() when this consumer's enable_count is non-zero.
	 *
	 * And it is very likely a no-op even then. drms_uA_update() returns 0
	 * without doing anything unless REGULATOR_CHANGE_DRMS is in the rail's
	 * valid_ops_mask, and of_get_regulation_constraints() sets that only
	 * for a node carrying "regulator-allow-set-load". Mainline's
	 * pmr735a_l1 has no such property, so this reports success and sends
	 * nothing: the LDO stays in whatever mode RPM last left it in, rather
	 * than being pushed into high power mode the way the stock 62 mA proxy
	 * pushes it (qcom,hpm-min-load is 10 mA). If the LDO's *mode* is what
	 * matters, this module cannot test it and the device tree has to say
	 * regulator-allow-set-load -- which is why patch 0121 adds it.
	 */
	ret = regulator_set_load(reg, ua);
	if (ret < 0)
		pr_err("rhodep_reghold: '%s': set_load %d uA failed %d\n",
		       name, ua, ret);
	else
		pr_info("rhodep_reghold: '%s': set_load %d uA returned %d "
			"(0 may mean 'no regulator-allow-set-load', see source)\n",
			name, ua, ret);

	reghold_report("held");
	return 0;
}

/* Caller locks. */
static void reghold_release(void)
{
	if (!reg || !enabled_by_us)
		return;

	regulator_set_load(reg, 0);
	regulator_disable(reg);
	enabled_by_us = false;
	reghold_report("released");
}

static bool hold = true;

static int reghold_set_hold(const char *val, const struct kernel_param *kp)
{
	bool want;
	int ret;

	ret = kstrtobool(val, &want);
	if (ret)
		return ret;

	guard(mutex)(&reghold_lock);

	if (want == hold)
		return 0;

	if (want) {
		ret = reghold_acquire();
		if (ret)
			return ret;
	} else {
		reghold_release();
	}

	hold = want;
	return 0;
}

static const struct kernel_param_ops reghold_hold_ops = {
	.set = reghold_set_hold,
	.get = param_get_bool,
};
module_param_cb(hold, &reghold_hold_ops, &hold, 0644);
MODULE_PARM_DESC(hold, "1 to hold the rail, 0 to release it (runtime writable)");

static int __init reghold_init(void)
{
	guard(mutex)(&reghold_lock);

	if (!uv_max)
		uv_max = uv;

	if (!hold) {
		/* Still attach, so that the state can be read without voting. */
		pr_info("rhodep_reghold: loaded idle (hold=0)\n");
		return reghold_attach();
	}

	return reghold_acquire();
}

/*
 * Not __exit, and in practice never called: see the header comment. Kept so
 * that the module is correct on a kernel where unloading works, but the
 * supported way to let go is `echo 0 > .../parameters/hold`.
 */
static void reghold_exit(void)
{
	guard(mutex)(&reghold_lock);

	reghold_release();
	if (reg) {
		regulator_put(reg);
		reg = NULL;
	}
	pr_info("rhodep_reghold: unloaded\n");
}

module_init(reghold_init);
module_exit(reghold_exit);
MODULE_DESCRIPTION("Hold a named regulator enabled at a voltage and load (rhodep diagnostic)");
MODULE_LICENSE("GPL");
