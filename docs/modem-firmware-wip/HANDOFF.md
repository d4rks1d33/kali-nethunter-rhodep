# The modem firmware, extracted

The modem is not what fails -- five participants have been measured healthy
through the LTE reset and its own DIAG logs show routine work to the last
instant. But it is still the one component never looked inside, and the reset
is something *asking* for a shutdown rather than anything failing, so the
firmware is worth a look before concluding it cannot be the asker.

## Getting it

The images are symlinks into the vendor partition, so pull from there. `scp`
with a wildcard returned a partial set twice -- 16 then 17 of 33 segments, with
no error -- so use tar, which does not silently truncate:

    ssh phone 'cd /readonly/firmware/image && tar cf - modem.b* modem.mdt' | tar xf -
    33 segments, 84 MB

## What it is

`modem.mdt` carries the ELF headers; the `.bNN` files are the segments.

    class     ELF32
    machine   164, QDSP6 (Hexagon)
    type      EXEC, entry 0x8b800000
    segments  36

    build     MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452

Segment 0 is the hash table, segment 1 is BOOT, and the rest are code and data
between 0xc0800000 and 0xce5b1000 plus a low region around 0x1e400000. The
largest are b26 at 37 MB, b21 at 14 MB and b20 at 9.5 MB. b21 holds 77462
strings and is the diagnostic message table -- format strings with the source
paths they came from, which is why DIAG output carries file names.

Disassembly needs a Hexagon-aware tool; binutils objdump here is not one.
LLVM's would work and is installable, and rizin has a Hexagon target.

## What is already visible without disassembling

**The firmware's address map contains PSHOLD.** In a table of hardware block
names, between the watchdog and the modem's own clock controller:

    ... QTIMR_AC QTIMR_V1 TSYNC WDOG MAPSS_APU PSHOLD MAPSS_CC TSENS0 ...

and the same table carries `PMIC_ARB_CORE`, `PMIC_ARB_CORE_REGISTERS`,
`SPMI_CFG` and `SPMI_PIC_OWNER`, so the modem knows where the PMIC arbiter is,
and `RPM_CODE`, `RPM_DATA`, `RPM_MSTR_MPU` and `RPMMasterLog` for the power
manager.

This proves nothing about the reset. A name table is a name table, and these
are generated wholesale from the SoC's register definitions, so their presence
says the modem was built for this chip rather than that it uses any of them. It
is worth writing down for one reason only: the remaining question is who pulls
PS_HOLD when neither Linux nor TrustZone does, and this is the first evidence
that the modem is even in a position to.

**`RPMMasterLog` is a lead worth taking.** The RPM keeps a per-master log of
what each processor asked for. `rpm_msg_ram` is at 0x45f0000 in the device tree
and `rhodep_dbgmem` can already read physical memory, so what the modem asked
the power manager for in its final seconds may be readable directly.
