# COTE dome controller firmware

`Main.c` is the LNA-supplied source of the OPD 40 cm dome controller — the
AT89S52 (8051) board this plugin's `DomeLNA` driver talks to over the serial
line. It is kept here as reference only: nothing in this repository builds it,
and the board is programmed with the vendor's Keil toolchain.

The file is in its original ISO-8859-1 encoding, with Portuguese comments. To
read it with the accents intact:

```sh
iconv -f ISO-8859-1 -t UTF-8 Main.c | less
```

## Why it is worth keeping

It is the only written source for the dome's tag geometry, which the driver
hard-codes:

| Firmware                    | Driver                                          |
| --------------------------- | ----------------------------------------------- |
| `ETIQINI 801`, `ETIQQTD 182` | tags 801-982 ([domelna.py](../../src/chimera_lna/instruments/domelna.py)) |
| `OFFSET 56`                 | position of the barcode reader (LCB) with the slit at North |
| `JOGSET 12`                 | distance from the target at which the inverter switches to JOG |
| `LCBTMO 6000`               | how long the board itself waits for a tag read   |
| `TRAPTMO 12000`             | slit open/close timeout, measured at 8 s         |

`LCBTMO` and `TRAPTMO` are the reason a healthy dome can stay silent for
several seconds: the board does not answer while it is waiting on the reader or
driving the slit. Any timeout on our side has to sit above them, which is part
of why #5 stops treating silence as a fault.

## Caveat: this is not the deployed revision

The command vocabulary here is `ABRIR`, `FECHAR`, `POSICAO?`, `GIRARCW`,
`GIRARCCW`, `ATIVARJOG`, `PARAR`, `INICIAR`, `MONITORAR`, `FLAT_ON`,
`FLAT_OFF`. The controller in the dome today speaks the `MEADE ...` protocol
(`MEADE PROG STATUS`, `MEADE DOMO MOVER = NNN`) that the driver and the
simulator implement. So treat this as a related revision of the same program —
authoritative for the hardware constants above, not for the wire protocol.
