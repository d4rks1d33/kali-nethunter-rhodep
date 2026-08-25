# 0078 was a duplicate of code that was already there

Written on the belief that mainline never tells the modem where its hashed
routing and filter tables are, while downstream does. That belief came from
grepping `init_modem_driver_req()` and stopping at line 345, which is exactly
where the function goes on to do it:

```c
	/* Nothing to report for the compression table (zip_tbl_info) */

	mem = ipa_mem_find(ipa, IPA_MEM_V4_ROUTE_HASHED);
	if (mem->size) {
		req.v4_hash_route_tbl_info_valid = 1;
		...
```

Upstream already sends all four hashed tables and both stats regions. The patch
set the same fields from the same layout a few lines earlier, and the existing
code then overwrote them with the same values. A no-op.

Worth recording the second mistake it caused. With the patch installed, an LTE
attach showed the modem starting channels 4 through 7 -- `2223222200000000`,
a pattern not seen before -- and registering with an IP. That looked like the
patch getting the modem further. It was not: the patch changes nothing, so that
was run-to-run variation being read as an effect.

Both errors have the same root: reading part of a file and concluding from the
part. The first cost a false alarm about the SRAM layout, this one cost a build
and a wrong claim.

What did come out of reading the rest of the function properly is real, and is
patch 0079.
