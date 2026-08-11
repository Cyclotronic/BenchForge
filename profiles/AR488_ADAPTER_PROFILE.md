# AR488 / AR488Lan GPIB Adapter Profile

> [!WARNING]
> **SOURCE-DERIVED — NOT HARDWARE-MEASURED.**
>
> Every other profile in this folder was probed against physical hardware. This one is read from the AR488 firmware source (`src/AR488/AR488.ino`, GitHub `Twilight-Logic/AR488`, master as of firmware **ver. 0.53.46**) and from TestController's client-side driver. **No AR488 has been on the bench.**
>
> Treat it as a specification to implement against, not as verified behaviour. Firmware versions differ; the version string alone will not match another build. Profile a physical adapter before relying on this for diagnosis.

---

## 1. Why AR488 is not a Prologix

The AR488 speaks a Prologix-*like* command set, but the differences are not cosmetic. An emulator that answers as a Prologix while claiming to be an AR488 will mislead a client in at least six ways.

| Behaviour | Prologix (measured) | AR488 (source) |
| :--- | :--- | :--- |
| **Command case** | **Case-sensitive.** `++AUTO` → `Unrecognized command` | **Case-INsensitive** — `strcasecmp(cmdHidx[i].token, token)` |
| **Token delimiter** | space only | **space or tab** — `strtok(buffr, " \t")` |
| **Error variety** | one message | **four**: `Unrecognized command`, `Invalid parameter`, `Missing parameter`, `Transmit failed!` |

> **Implemented, source-derived — needs hardware confirmation.** An argument
> rejection on a command the AR488 shares with the Prologix (`++addr 99`,
> `++eos 9`, `++read_tmo_ms 99999`, or an argument passed to a command that
> takes none) now answers `Invalid parameter`, per `errorMsg()` case 2 in
> `AR488.ino`. An unknown *command* still answers `Unrecognized command`.
>
> This is the one place where the AR488 persona deliberately diverges from its
> Prologix parent on a shared code path, so it is the first thing to check when
> an adapter reaches the bench. `test_21` pins the current behaviour; if the
> hardware disagrees, that test is what should fail.
| `++default` | `Unrecognized command` | **supported** — resets to controller defaults; optional `wipe` erases EEPROM |
| `++read_tmo_ms` range | 1–3000 | **1–32000** |
| `++auto` range | 0–1 | **0–3** |
| Secondary address | 96–126 | **31–126** |
| `++ver` | `Prologix GPIB-ETHERNET Controller version 01.06.06.00` | `AR488 GPIB controller, ver. 0.53.46, 22/05/2026` (or a user string set via `++setvstr`) |

### Response terminator
`errorMsg()` and the command handlers all emit through `dataPort.println()`. Arduino's `Print::println()` writes **`\r\n`**, so AR488 controller replies are CRLF-terminated — the same as Prologix. (An earlier reading of this as bare `\n` was wrong; `println()` is not `write('\n')`.)

```c
void errorMsg(uint8_t err) {
  switch (err) {
    case 1:  dataPort.println(F("Missing parameter")); break;
    case 2:  dataPort.println(F("Invalid parameter")); break;
    case 3:  dataPort.println(F("Transmit failed!")); break;
    default: dataPort.println(F("Unrecognized command"));
  }
}
```

---

## 2. Command set (source)

Mode column: 1 = device, 2 = controller, 3 = both.

| Command | Mode | Parameters | In Prologix? |
| :--- | :---: | :--- | :---: |
| `addr` | 3 | primary 0–30, optional secondary 31–126 | yes |
| `auto` | 2 | 0–3 | yes (0–1) |
| `clr` | 2 | — | yes |
| `dcl` | 2 | — | **no** |
| `default` | 3 | optional `wipe` | **no** |
| `eoi` | 3 | 0–1 | yes |
| `eor` | 3 | 0–7 | **no** |
| `eos` | 3 | 0–3 | yes |
| `eot_char` | 3 | 0–255 | yes |
| `eot_enable` | 3 | 0–1 | yes |
| `flags` | 2 | 0–7, optional count | **no** |
| `fndl` | 2 | optional addresses/range | **no** |
| `help` | 3 | optional keyword | yes |
| `ifc` | 2 | — | yes |
| `id` | 3 | `name`/`serial`/`verstr` + data | **no** |
| `idn` | 3 | 0–2 | **no** |
| `llo` | 2 | optional `all` | yes |
| `loc` | 2 | optional `all` | yes |
| `lon` | 1 | 0–1 | **no** (Prologix rejects) |
| `macro` | 2 | 0–9 | **no** |
| `mode` | 3 | 0–1 | yes |
| `ppoll` | 2 | — | **no** |
| `prom` | 1 | 0–1 | **no** |
| `read` | 2 | optional `@address:endtype` | yes (different syntax) |
| `read_tmo_ms` | 2 | 1–32000 | yes (1–3000) |
| `ren` | 2 | 0–1 | **no** |
| `repeat` | 2 | count delay command | **no** |
| `rst` | 3 | — | yes |
| `send` | 2 | address, optional secondary, data | **no** |
| `setvstr` | 3 | string, max 47 chars | **no** |
| `spoll` | 2 | optional `all` or addresses | yes |
| `srq` | 2 | — | yes |
| `srqauto` | 2 | 0–1 | **no** |
| `status` | 1 | 0–255 | **no** (Prologix rejects) |
| `tct` | 2 | 0–30 | **no** |
| `ton` | 1 | 0–2 | **no** |
| `trg` | 2 | optional addresses | yes |
| `unl` | 2 | — | **no** |
| `unt` | 2 | — | **no** |
| `ver` | 3 | optional `real` | yes |
| `verbose` | 3 | — | **no** |
| `xdiag` | 3 | mode byte | **no** |

### `++read` syntax
AR488 accepts `++read`, `++read eoi`, `++read @<address>`, and `++read @<address>:eoi` or `++read @<address>:0x##`. The `@address` form has no Prologix equivalent.

### ESC escaping
Handled as on Prologix: an escaped CR/LF is buffered as data rather than terminating the line, and an escaped `+` prevents command detection when it falls within the first two characters.

---

## 3. What TestController sends (client side)

From `dk.hkj.shared.SharedInterfaceAR488` in the decompiled reference:

```java
init():            ++default                       // Prologix sends ++auto 0 ; ++mode 1
writeControl():    ++CLR  ++LLO  ++LOC  ++TRG      // UPPERCASE
writeReadBin():    escape(msg, 27, "+\r\n")
                   ++read <eoi|char>
write():           escape(msg, 27, "+")
setActualAddress:  ++addr <n>       (only when it changes)
setActualTimeout:  ++read_tmo_ms <n>
```

The uppercase control commands only work because the AR488 is case-insensitive — sending those to a Prologix would return `Unrecognized command`.

### AR488Lan transport
`SharedInterfaceAR488Lan.getPort()` defaults to **port 23** and is overridden by a `port:<n>` element in the interface settings. This is not port 1234.

---

## 4. To profile a physical adapter

Priority order, mirroring what was measured for the Prologix:

1. `++ver` — exact string for the firmware in use.
2. Case handling — `++AUTO`, `++Addr` (expected: accepted).
3. Error variants — which input produces `Invalid parameter` vs `Missing parameter` vs `Unrecognized command`.
4. Terminator on controller replies and on relayed instrument data.
5. `++read_tmo_ms 32000` and `++auto 2` / `++auto 3` — confirm the wider ranges.
6. `++default` effect on the other settings.
7. Whether relayed instrument data passes through byte for byte, as the Prologix does.
