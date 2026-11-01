"""YM2612 (OPN2) register-to-channel attribution for native evidence.

Mirrors the OPNA FM core layout: the two ports select channel triples
1-3 and 4-6, operator registers live at 0x30-0x9F, channel registers
at 0xA0-0xB6, and the key on/off register 0x28 encodes the channel in
its low bits. Global registers (LFO 0x22, timers, ch3 mode 0x27) and
the DAC registers (0x2A/0x2B) belong to no single channel here; the
DAC channel attribution arrives with the DAC stage.
"""


def register_channel(register, data, port=0):
    if port == 0 and register == 0x28:
        local = data & 3
        return None if local == 3 else local + (3 if data & 4 else 0)
    if 0x30 <= register <= 0x9f and register & 3 != 3:
        return (register & 3) + port * 3
    if (0xa0 <= register <= 0xa2 or 0xa4 <= register <= 0xa6
            or 0xb0 <= register <= 0xb2 or 0xb4 <= register <= 0xb6):
        return (register & 3) + port * 3
    if port == 0 and (0xa8 <= register <= 0xaa or 0xac <= register <= 0xae):
        return 2
    return None
