# memshare responder

The modem asks the application processor for shared memory over QMI service 52
during bring-up. Mainline provides no memshare at all, so nothing answered, and
the modem's own state machine never finished initialising: it answers queries
and reports its IMEI, but refuses **every** operating mode transition and the
USIM stops at `check-personalization-state`.

Captured on this device at boot, with the request decoded:

	MEM_QUERY_SIZE (0x0024) txn=1     client_id=1 proc=0
	MEM_ALLOC_GENERIC (0x0022) txn=2  num_bytes=0x500000 client_id=1
	                                  proc_id=0 sequence_id=0 alloc_contiguous=1

Told there is no memory it carries on unchanged. Told there are 5 MB it asks for
them immediately, which is what proves it wants the memory itself rather than
just a reply.

## Two halves, and both are needed

**Kernel side**, a reserved region so Linux does not use that memory as ordinary
RAM. It is a device tree change, so it needs a new boot image:

	memshare@8ab00000 {
		reg = <0x00 0x8ab00000 0x00 0x800000>;
		no-map;
	};

8 MB in the hole between `pil-gpu-ucode` and `pil-mpss-wlan`. Stock places its
`memshare_region` dynamically as a `shared-dma-pool`; a fixed address is used
here so the responder can stay in userspace, with nothing to allocate and only a
number to hand over.

**Userspace side**, this daemon, which publishes service 52 and answers.

## Install

	sudo apt install libqrtr-dev
	sudo ./install.sh

By default the daemon reports **zero bytes available**, which is the honest
answer on an image whose device tree has no reserved region: promising memory
and then failing the allocation is worse than saying up front there is none. To
actually hand out the region, run it with the size to advertise:

	ExecStart=/usr/local/bin/rhodep-memshare 0x500000

## Status

Answering the query is confirmed to work and is **not sufficient on its own**;
the modem stays offline. Whether satisfying the allocation is sufficient is the
open question, and it is the last part of this exchange still missing. See
`docs/interconnect-sm6375-wip/HANDOFF-SESSION4.md`, sessions 13 and 13b.
