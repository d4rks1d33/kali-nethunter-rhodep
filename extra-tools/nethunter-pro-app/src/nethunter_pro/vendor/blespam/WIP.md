# BLE Spam — Work In Progress

Status: **WIP / partially working**

## What works
* `radio.py` – takes the radio away from bluetoothd cleanly. Verified: after
  `_lock_bluetoothd()` the daemon stays down and hci0 is under the HCI user
  channel. No more 0x12 / 0x0C storm from a competing bluetoothd.
* `hci.py` – Legacy Advertising helpers (0x2006/0x2008/0x2009/0x200A) *plus*
  new Extended Advertising helpers (0x2035/0x2036/0x2037/0x2038/0x2039 +
  0x203D Clear Advertising Sets). Extended is the only path the WCN399x
  firmware on this SoC actually honours – Legacy commands complete with
  `Status: Success` on the HCI, but the radio never keys on air. Discovered
  by comparing btmon captures against `hciconfig hciX leadv` (also returns
  `status 12` on this chip). Confirmed working manually with `hcitool -i
  hci0 cmd 0x08 0x0039 01 01 00 00 00 00`: an nRF-style scanner saw the
  set immediately.
* `engine.py` – re-plumbed to use Extended Advertising. Order matters: the
  set must be created (`Set Extended Advertising Parameters`, 0x2036)
  *before* binding a random address (`Set Advertising Set Random Address`,
  0x2035) or the controller returns 0x42 “Advertising Set Not Found”.
  Uses `EXT_ADV_PROPS_NONCONN_IND` (0x0010 – LEGACY, non-scannable,
  non-connectable) which matches the only combo the manual test proved
  the QCA firmware will actually transmit.

## What still doesn’t work
* End-to-end visibility on a scanner. The engine now runs for ≥45 s without
  errors, emits `packet` events at the configured rate, and the HCI shows
  `commands: NNNN errors: 0` – but nothing shows up on Android nRF Connect
  or on iOS device pickers. Manual `hcitool` with the *same* Extended
  Advertising Enable command was picked up by nRF Connect within a second
  when tested by hand, so the packet-crafting side is likely fine and the
  regression is in one of:

    1. **`adv_event_props` layout**. Manual test used 0x0010; engine also
       uses 0x0010 now, but the byte order in `set_extended_advertising_
       parameters()` may be off by one. Double-check the 3-byte primary
       advertising interval (`.to_bytes(3, "little")`) and the signed TX
       power field.
    2. **Handle mismatch**. Engine uses `handle=0` everywhere. Confirm
       `Set Advertising Set Random Address(handle=0, addr=…)` and
       `Set Extended Advertising Enable(enable=1, sets=1, handle=0, …)`
       both refer to the same set the params created.
    3. **Payload size**. `set_extended_advertising_data()` truncates to
       31 bytes; some Apple Continuity payloads are exactly 31 bytes –
       verify the `data_length` field matches the raw byte count
       (see the “0x11 vs 0x0F” miscount that ate two rounds of debugging
       during the port).
    4. **Coex/PSCAN**. When bluetoothd is masked, hci0 stays `UP RUNNING`
       (no PSCAN/ISCAN) which should be fine, but the QCA coex firmware
       may quietly drop LE TX when there’s no active BR/EDR context.
       Try enabling `Write Scan Enable (0x03|0x001a)` = 0x02 (page scan
       only) *before* starting the advertising set, or a `Vendor
       (0x3f|0x???)` coex-enable command the vendor driver would issue.

## How to bisect next
1. `sudo pkill bluetoothd; sudo hcitool -i hci0 cmd 0x08 0x0039 00 00`
   to make sure the chip is idle.
2. Run the exact sequence that worked by hand, capturing btmon:

    ```
    sudo btmon -w /tmp/manual.log &
    sudo hcitool -i hci0 cmd 0x03 0x0003                    # Reset
    sudo hcitool -i hci0 cmd 0x08 0x003D                    # Clear sets
    sudo hcitool -i hci0 cmd 0x08 0x0036 00 10 00 A0 00 00 A0 00 00 07 01 00 \
                                        00 00 00 00 00 00 00 7F 01 00 01 00 00
    sudo hcitool -i hci0 cmd 0x08 0x0035 00 55 44 33 22 11 C0
    sudo hcitool -i hci0 cmd 0x08 0x0037 00 03 01 11 02 01 06 0B 09 6B 61 6C \
                                        69 2D 62 6C 65 73 70 61 6D
    sudo hcitool -i hci0 cmd 0x08 0x0039 01 01 00 00 00 00
    ```

    nRF Connect should show `kali-blespam` at RSSI ~-40 within a second.

3. Then run the engine with the *same* payload and compare btmon output
   byte-for-byte. The first divergent byte identifies the bug.

## Related
* `radio.py` fix (bluetoothd lockout with mask + kill + verify) already
  landed and is verified working – do **not** revert.
* WiFi Beacon Spam (`modules/wifi_beacon_spam.py`) works and is unrelated;
  the sudoers `Process(root=True)` fix from that module is the pattern
  we should keep for any future BLE-spam CLI.
