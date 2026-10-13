/* Polled USB vendor-class device on the WL82 full-speed controller JL_USB
 * (0x11800), for the Oscuro File Bridge prototype (firmware/ram-lvgl/
 * usbfiles.c). This is a trimmed copy of the register handling in usbdev.c:
 * the same SDK-derived MUSB sequences, but a single vendor interface with a
 * bulk IN/OUT pair on EP1 and no MIDI/HID/audio and no interrupts.
 *
 * Why a copy and not smk37_usb: the shipping core targets a MIDI device and is
 * host-tested byte-for-byte; the prototype wants a self-contained vendor
 * descriptor and must not perturb that. Everything here is prototype-only RAM
 * code, run from ROM UBOOT and thrown away on power-off.
 *
 * Register notes (identical to usbdev.c, which cites cpu.a usb.c.o):
 *   - MUSB core registers are reached indirectly through USB_CON1:
 *     write = addr << 8 | data, read = addr << 8 | 0x4000 (MC_RNW), then wait
 *     for bit 15 (MACK); only while CON0 bit 2 (USB_NRST) is set.
 *   - Endpoint data moves by DMA through RAM buffers: EP0_ADR, EPn_TADR/RADR,
 *     byte count for IN packets in EPn_CNT.
 *   - Bulk endpoint config: TXMAXP/RXMAXP 0xFF, TXCSR 0x48, RXCSR 0x90.
 *   - Detach = CON0 = 0 (drop D+), attach = the usb_g_sie_init sequence.
 */
#include "usbven.h"

#include <string.h>

void wdt_clear(void);

#define REG(a) (*(volatile uint32_t *)(a))
#define USB_CON0 REG(0x11800)
#define USB_CON1 REG(0x11804)
#define USB_EP0_CNT REG(0x11808)
#define USB_EP1_CNT REG(0x1180c)
#define USB_EP0_ADR REG(0x11818)
#define USB_EP1_TADR REG(0x1181c)
#define USB_EP1_RADR REG(0x11820)
#define USB_IO_CON0 REG(0x51000)
#define C0_CK REG(0x1eee218)

enum {
    M_FADDR = 0, M_POWER = 1, M_INTRTX1 = 2, M_INTRRX1 = 4, M_INTRUSB = 6,
    M_INTRTX1E = 7, M_INTRTX2E = 8, M_INTRRX1E = 9, M_INTRRX2E = 10, M_INTRUSBE = 11,
    M_INDEX = 14, M_TXMAXP = 16, M_CSR0 = 17, M_TXCSR1 = 17, M_TXCSR2 = 18, M_RXMAXP = 19,
    M_RXCSR1 = 20, M_RXCSR2 = 21, M_COUNT0 = 22, M_RXCOUNT1 = 22, M_RXCOUNT2 = 23,
};

#define EP_BULK 1                /* EP1: OUT (host -> device) and IN (device -> host) */
#define EP_ADDR_OUT 0x01
#define EP_ADDR_IN 0x81

usbven_stats_t usbven_stats;

static uint8_t ep0_buf[64] __attribute__((aligned(4)));
static uint8_t ep0_reply[2] __attribute__((aligned(4)));   /* small control replies, never aliases ep0_buf */
static uint8_t ep1_tx[64] __attribute__((aligned(4)));
static uint8_t ep1_rx[64] __attribute__((aligned(4)));

static enum { EP0_IDLE, EP0_TX } ep0_phase;
static const uint8_t *ep0_ptr;
static uint16_t ep0_left;
static bool ep0_zlp, addr_pending, configured;

static inline void csync(void) { __asm__ volatile("csync"); }
static inline void ssync(void) { __asm__ volatile("ssync"); }

/* ---- descriptor ----------------------------------------------------------- */

#define OF_VID 0x1209   /* pid.codes shared test space; 1209:0001 is the MIDI device */
#define OF_PID 0x0003

static const uint8_t dev_desc[18] = {
    18, 1, 0x00, 0x01,               /* USB 1.1, full speed only */
    0xFF, 0x00, 0x00,                /* vendor specific at device level */
    64,                              /* bMaxPacketSize0 */
    0x09, 0x12, 0x03, 0x00,          /* VID 0x1209, PID 0x0003 */
    0x00, 0x01,                      /* bcdDevice 1.00 */
    1, 2, 0,                         /* manufacturer, product, no serial */
    1,
};

static const uint8_t cfg_desc[32] = {
    9, 2, 32, 0, 1, 1, 0, 0x80, 50,  /* 1 interface, bus powered, 100 mA */
    9, 4, 0, 0, 2, 0xFF, 0x00, 0x00, 0,        /* interface 0: vendor specific */
    7, 5, EP_ADDR_OUT, 2, 64, 0, 0,            /* EP1 OUT, bulk, 64 */
    7, 5, EP_ADDR_IN, 2, 64, 0, 0,             /* EP1 IN, bulk, 64 */
};

static const uint8_t str_lang[4] = { 4, 3, 0x09, 0x04 };
static const uint8_t str_mfr[14] = { 14, 3,
    'O', 0, 's', 0, 'c', 0, 'u', 0, 'r', 0, 'o', 0 };
static const uint8_t str_prod[38] = { 38, 3,
    'O', 0, 's', 0, 'c', 0, 'u', 0, 'r', 0, 'o', 0, ' ', 0,
    'F', 0, 'i', 0, 'l', 0, 'e', 0, ' ', 0, 'B', 0, 'r', 0, 'i', 0, 'd', 0, 'g', 0, 'e', 0 };

/* ---- MUSB window ------------------------------------------------------------ */

static uint32_t mread(uint32_t addr)
{
    if (!(USB_CON0 & 4u))
        return 0;
    USB_CON1 = ((addr << 8) & 0xFFBF00u) | 0x4000u;
    csync();
    for (unsigned ot = 20000; !(USB_CON1 & 0x8000u);)
        if (!ot--)
            return 0;
    return USB_CON1 & 0xFFu;
}

static void mwrite(uint32_t addr, uint32_t data)
{
    if (!(USB_CON0 & 4u))
        return;
    USB_CON1 = ((addr << 8) & 0xFFFF00u) | (data & 0xFFu);
    csync();
    for (unsigned ot = 20000; !(USB_CON1 & 0x8000u);)
        if (!ot--)
            return;
}

static uint32_t read_csr(uint32_t ep, uint32_t lo, uint32_t hi)
{
    mwrite(M_INDEX, ep);
    return mread(lo) | mread(hi) << 8;
}

static void write_csr(uint32_t ep, uint32_t lo, uint32_t hi, uint32_t v)
{
    mwrite(M_INDEX, ep);
    mwrite(lo, v & 0xFFu);
    mwrite(hi, v >> 8);
}

static uint32_t read_csr0(void) { mwrite(M_INDEX, 0); return mread(M_CSR0); }
static void write_csr0(uint32_t v) { mwrite(M_INDEX, 0); mwrite(M_CSR0, v); }
static uint32_t read_txcsr1(void) { return read_csr(EP_BULK, M_TXCSR1, M_TXCSR2); }
static void write_txcsr1(uint32_t v) { write_csr(EP_BULK, M_TXCSR1, M_TXCSR2, v); }
static uint32_t read_rxcsr1(void) { return read_csr(EP_BULK, M_RXCSR1, M_RXCSR2); }
static void write_rxcsr1(uint32_t v) { write_csr(EP_BULK, M_RXCSR1, M_RXCSR2, v); }

/* Coarse busy delay. Does not trust any hardware counter: the ROM clock
 * source is not guaranteed in a UBOOT context (udisk.c makes the same call). */
static void wait_us(uint32_t cpu_mhz, uint32_t us)
{
    volatile uint32_t n = cpu_mhz * us / 3u;
    for (volatile uint32_t i = 0; i < n; i++)
        if (!(i & 0xffffu))
            wdt_clear();
}

static void enable_irqs(void)
{
    mwrite(M_INTRUSBE, 0x04 | 0x01);                 /* reset, suspend */
    mwrite(M_INTRTX1E, configured ? 0x03 : 0x01);    /* EP0, EP1 IN */
    mwrite(M_INTRTX2E, 0);
    mwrite(M_INTRRX1E, configured ? 0x02 : 0x00);    /* EP1 OUT */
    mwrite(M_INTRRX2E, 0);
}

static void ep1_config(void)
{
    USB_EP1_TADR = (uint32_t)ep1_tx;
    csync();
    mwrite(M_INDEX, EP_BULK);
    mwrite(M_TXMAXP, 0xFF);
    write_txcsr1(0x48);                              /* clear data toggle, flush */
    USB_EP1_RADR = (uint32_t)ep1_rx;
    csync();
    mwrite(M_INDEX, EP_BULK);
    mwrite(M_RXMAXP, 0xFF);
    write_rxcsr1(0x90);
    configured = true;
    enable_irqs();
}

static void bus_reset(void)
{
    usbven_stats.resets++;
    usbven_stats.stage = 3;
    ep0_phase = EP0_IDLE;
    addr_pending = false;
    configured = false;
    USB_EP0_ADR = (uint32_t)ep0_buf;
    csync();
    enable_irqs();
}

/* ---- EP0 -------------------------------------------------------------------- */

static void ep0_stage_tx(void)
{
    if (read_csr0() & 2u)                            /* previous packet not sent */
        return;
    unsigned n = ep0_left < 64 ? ep0_left : 64;
    memcpy(ep0_buf, ep0_ptr, n);
    csync();
    USB_EP0_CNT = n;
    csync();
    ep0_ptr += n;
    ep0_left = (uint16_t)(ep0_left - n);
    if (ep0_left || (n == 64 && ep0_zlp)) {
        if (!ep0_left)
            ep0_zlp = false;
        write_csr0(0x02);
    } else {
        write_csr0(0x0A);                            /* TxPktRdy | DataEnd */
        ep0_phase = EP0_IDLE;
    }
}

static bool ep_ok(uint8_t addr)
{
    return addr == EP_ADDR_OUT || addr == EP_ADDR_IN;
}

/* One 8-byte setup packet. Returns true when the reply was served inline. */
static void ep0_event(void)
{
    if (addr_pending) {
        mwrite(M_FADDR, usbven_stats.address);
        addr_pending = false;
    }
    uint32_t csr0 = read_csr0();
    if (csr0 & 0x04u) {                              /* SentStall */
        write_csr0(0);
        ep0_phase = EP0_IDLE;
        return;
    }
    if (csr0 & 0x10u) {                              /* SetupEnd */
        write_csr0(0x80);
        ep0_phase = EP0_IDLE;
    }
    if (ep0_phase == EP0_TX) {
        ep0_stage_tx();
        return;
    }
    if (!(csr0 & 0x01u))                             /* RxPktRdy */
        return;

    uint8_t setup[8];
    ssync();
    memcpy(setup, ep0_buf, 8);
    usbven_stats.setups++;
    usbven_stats.last_type = setup[0];
    usbven_stats.last_req = setup[1];

    uint8_t type = setup[0], req = setup[1];
    uint16_t value = (uint16_t)(setup[2] | setup[3] << 8);
    uint16_t index = (uint16_t)(setup[4] | setup[5] << 8);
    uint16_t wlength = (uint16_t)(setup[6] | setup[7] << 8);
    bool in = type & 0x80;
    uint8_t recip = type & 0x1F;

    if ((type & 0x60) != 0)                          /* class/vendor requests: none */
        goto stall;

    switch (req) {
    case 6: {                                        /* GET_DESCRIPTOR */
        const uint8_t *d = 0;
        uint16_t n = 0;
        if (!in || recip != 0)
            goto stall;
        switch (value >> 8) {
        case 1: d = dev_desc; n = sizeof dev_desc; break;
        case 2:
            if ((value & 0xFF) != 0)
                goto stall;
            d = cfg_desc; n = sizeof cfg_desc;
            break;
        case 3:
            switch (value & 0xFF) {
            case 0: d = str_lang; n = sizeof str_lang; break;
            case 1: d = str_mfr; n = sizeof str_mfr; break;
            case 2: d = str_prod; n = sizeof str_prod; break;
            default: goto stall;
            }
            break;
        default: goto stall;
        }
        write_csr0(0x40);                            /* ServicedRxPktRdy */
        ep0_ptr = d;
        ep0_left = n < wlength ? n : wlength;
        ep0_zlp = ep0_left < wlength && ep0_left % 64 == 0;
        ep0_phase = EP0_TX;
        ep0_stage_tx();
        return;
    }
    case 5:                                          /* SET_ADDRESS */
        if (in || recip != 0 || value > 127)
            goto stall;
        usbven_stats.address = (uint8_t)value;
        addr_pending = true;
        write_csr0(0x48);
        return;
    case 9:                                          /* SET_CONFIGURATION */
        if (in || recip != 0 || value > 1)
            goto stall;
        usbven_stats.configured = (uint8_t)value;
        if (value == 1)
            ep1_config();
        else {
            configured = false;
            enable_irqs();
        }
        write_csr0(0x48);
        return;
    case 8:                                          /* GET_CONFIGURATION */
        if (!in || recip != 0)
            goto stall;
        ep0_reply[0] = usbven_stats.configured;
        write_csr0(0x40);
        ep0_ptr = ep0_reply;
        ep0_left = 1;
        ep0_zlp = false;
        ep0_phase = EP0_TX;
        ep0_stage_tx();
        return;
    case 0:                                          /* GET_STATUS */
        if (!in)
            goto stall;
        if (recip == 1 && (index & 0xFF) > 0)
            goto stall;
        if (recip == 2 && (index & 0x7F) && !ep_ok((uint8_t)index))
            goto stall;
        if (recip > 2)
            goto stall;
        ep0_reply[0] = ep0_reply[1] = 0;
        write_csr0(0x40);
        ep0_ptr = ep0_reply;
        ep0_left = 2;
        ep0_zlp = false;
        ep0_phase = EP0_TX;
        ep0_stage_tx();
        return;
    case 10:                                         /* GET_INTERFACE */
        if (!in || recip != 1 || (index & 0xFF) > 0 || !usbven_stats.configured)
            goto stall;
        ep0_reply[0] = 0;
        write_csr0(0x40);
        ep0_ptr = ep0_reply;
        ep0_left = 1;
        ep0_zlp = false;
        ep0_phase = EP0_TX;
        ep0_stage_tx();
        return;
    case 11:                                         /* SET_INTERFACE (alternate 0 only) */
        if (in || recip != 1 || (index & 0xFF) > 0 || !usbven_stats.configured || value != 0)
            goto stall;
        write_csr0(0x48);
        return;
    case 1:                                          /* CLEAR_FEATURE */
    case 3:                                          /* SET_FEATURE */
        if (in)
            goto stall;
        if (recip == 2 && value == 0 && (index & 0x7F) && ep_ok((uint8_t)index)) {
            if (req == 1) {
                if (index == EP_ADDR_IN)
                    write_txcsr1(0x40);              /* ClrDataTog */
                else
                    write_rxcsr1(0x80);
            }
            write_csr0(0x48);
            return;
        }
        if (recip == 0 && value == 1) {              /* DEVICE_REMOTE_WAKEUP: ignored */
            write_csr0(0x48);
            return;
        }
        goto stall;
    default:
        goto stall;
    }

stall:
    usbven_stats.stalls++;
    write_csr0(0x60);                                /* ServicedRxPktRdy | SendStall */
}

/* ---- public ----------------------------------------------------------------- */

void usbven_start(uint32_t cpu_mhz, uint32_t detach_ms)
{
    memset(&usbven_stats, 0, sizeof usbven_stats);
    ep0_phase = EP0_IDLE;
    addr_pending = configured = false;

    /* Detach whatever owned the port (UBOOT's mass-storage device). */
    USB_CON0 = 0;
    USB_CON1 = 0;
    USB_IO_CON0 = 12;
    usbven_stats.stage = 1;
    wait_us(cpu_mhz, detach_ms * 1000u);

    /* usb_g_sie_init */
    USB_IO_CON0 |= 1u << 9 | 1u << 10;
    USB_IO_CON0 &= ~(1u << 11);
    USB_IO_CON0 |= 1u << 12;
    USB_CON0 &= ~0x4035u;
    USB_CON1 = 0;
    USB_CON0 |= 1u;
    USB_IO_CON0 &= ~0x30u;
    USB_IO_CON0 &= ~0xC0u;
    USB_IO_CON0 |= 0x40u;                            /* D+ pull-up: full-speed device */
    USB_CON0 |= 0x3Cu;
    /* usb_device_init */
    mwrite(M_POWER, 0x60);
    USB_EP0_ADR = (uint32_t)ep0_buf;
    csync();
    enable_irqs();
    usbven_stats.stage = 2;
}

void usbven_poll(void)
{
    if (!(USB_CON0 & 4u))
        return;
    uint32_t intr_usb = mread(M_INTRUSB);
    uint32_t intr_tx = mread(M_INTRTX1);
    (void)mread(M_INTRRX1);
    if (intr_usb & 0x04u)
        bus_reset();
    if (intr_tx & 0x01u)
        ep0_event();
}

bool usbven_configured(void)
{
    return configured;
}

bool usbven_tx_ready(void)
{
    return configured && !(read_txcsr1() & 1u);
}

void usbven_tx(const uint8_t *data, unsigned len)
{
    uint32_t txcsr = read_txcsr1();
    if (!configured || (txcsr & 1u) || len > 64) {
        usbven_stats.tx_busy++;
        return;
    }
    memcpy(ep1_tx, data, len);
    csync();
    USB_EP1_TADR = (uint32_t)ep1_tx;
    csync();
    USB_EP1_CNT = len;
    csync();
    write_txcsr1(txcsr | 1u);
    usbven_stats.tx_packets++;
}

unsigned usbven_rx(uint8_t *buf)
{
    if (!configured)
        return 0;
    uint32_t rxcsr = read_rxcsr1();
    if (!(rxcsr & 1u))                               /* RxPktRdy */
        return 0;
    mwrite(M_INDEX, EP_BULK);
    unsigned n = mread(M_RXCOUNT1) | mread(M_RXCOUNT2) << 8;
    if (n > 64)
        n = 64;
    ssync();
    memcpy(buf, ep1_rx, n);
    write_rxcsr1((rxcsr & ~0x165u) | 0x10u);          /* release the buffer */
    usbven_stats.rx_packets++;
    return n;
}
