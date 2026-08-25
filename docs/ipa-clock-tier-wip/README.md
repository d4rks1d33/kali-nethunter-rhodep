# The IPA core clock tier: a real divergence, not the bug

Downstream scales the IPA core clock across four tiers and picks one from
measured throughput:

```
IPA_V4_0_CLK_RATE_SVS2		 60 MHz
IPA_V4_0_CLK_RATE_SVS		125 MHz
IPA_V4_0_CLK_RATE_NOMINAL	220 MHz
IPA_V4_0_CLK_RATE_TURBO		250 MHz
```

Mainline has a single number, `core_clock_rate = 60 * 1000 * 1000`, which is
the bottom tier, held forever. That is a genuine difference from the vendor and
it does mean this port can never reach downstream's throughput.

It is not what kills LTE. Built with the nominal tier and tested: GPRS still
carries traffic, `http=204`, and the LTE attach still takes the SoC down on the
same schedule.

Parked rather than kept. Pinning 220 MHz costs idle power on a link that never
needs it, and the right shape is what downstream does -- scale with demand --
which is worth doing for its own sake if this port ever gets a working LTE to
be slow on.
