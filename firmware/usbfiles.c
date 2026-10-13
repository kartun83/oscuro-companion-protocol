/* SPDX-License-Identifier: Apache-2.0 */
/* Copyright 2026 Aleksei Tveritinov */

/* Oscuro File Bridge — keyboard side (RAM payload run from ROM UBOOT1.00).
 *
 * A minimal LVGL UI with a scrolling "file" list, plus a vendor-class USB
 * device (usbven.c) that speaks the Oscuro File Bridge protocol
 * (usbfiles_proto.h) over a bulk pipe. The host is the smartphone (or a Mac /
 * the Python mock); this side is the USB device.
 *
 * Both directions are exercised at once:
 *   - the host's list arrives over bulk and is shown in the scrolling window
 *     (host -> device), and
 *   - the device answers LIST_REQ with its own fake file list (device -> host).
 *
 * The UI deliberately uses only plain labels on the screen (no lv_obj
 * container/scroll): that is the pattern proven on the unit by the Oscuro
 * usb-stick payload (firmware/ram-lvgl/udisk.c, LVGL + a USB takeover from
 * UBOOT). Scrolling is done by re-labelling a fixed row pool.
 *
 * Nothing here touches flash. Execution takes the USB port over and never
 * returns to UBOOT; power-cycle to leave. See docs/proto/USB_FILE_BRIDGE.md.
 */
#include "lvgl.h"

#include <string.h>

#include "panel.h"
#include "usbfiles_proto.h"
#include "usbven.h"

void wdt_clear(void);
int panel_init(void);
int panel_write(int x1, int y1, int x2, int y2, const uint16_t *pixels);
void of_flush(lv_display_t *disp, const lv_area_t *a, uint8_t *buffer);

/* ---- fake file list the device serves to the host ------------------------- */

static const struct {
    const char *name;
    uint32_t size;
} dev_files[] = {
    { "LIVE_SET.MID", 2048u },
    { "PATCH_01.SYX", 512u },
    { "SONG_A.WAV", 1048576u },
    { "SCALE.SCL", 128u },
    { "README.TXT", 300u },
    { "DRUMS.OSCBANK", 65536u },
};
#define DEV_FILES_N (sizeof dev_files / sizeof dev_files[0])

/* ---- state ----------------------------------------------------------------- */

typedef struct {
    char name[OF_NAME_MAX + 1];
    uint32_t size;
    uint8_t kind;
} of_entry_t;

static of_entry_t host_files[OF_FILES_MAX];
static unsigned host_count;
static bool assembling;
static char peer_name[33];
static bool have_peer;
static uint32_t served_ok;
static bool was_configured;
static unsigned row_off;                          /* first visible row */

/* outgoing message queue (small; a list response is up to 1 + N + 1 frames) */
#define TXQ_SLOTS (DEV_FILES_N + 4)
static uint8_t txq[TXQ_SLOTS][OF_PKT_MAX];
static uint8_t txq_len[TXQ_SLOTS];
static unsigned txq_head, txq_tail, txq_n;

/* ---- UI (plain labels only) ------------------------------------------------ */

#define ROWS 9
static lv_obj_t *row_lbl[ROWS];
static lv_obj_t *status_lbl;
static bool ui_ready;

static void ui_status(void)
{
    if (!ui_ready)
        return;
    char buf[96];
    lv_snprintf(buf, sizeof buf, "%s RX:%u TX:%u",
                have_peer ? peer_name : "waiting for host",
                host_count, (unsigned)served_ok);
    lv_label_set_text(status_lbl, buf);
}

/* Relabel the visible window from host_files[row_off .. row_off+ROWS). */
static void ui_rows(void)
{
    if (!ui_ready)
        return;
    char line[OF_NAME_MAX + 24];
    if (host_count == 0) {
        lv_label_set_text(row_lbl[0], "(no host list yet)");
        for (unsigned i = 1; i < ROWS; i++)
            lv_label_set_text(row_lbl[i], "");
    } else {
        for (unsigned i = 0; i < ROWS; i++) {
            const of_entry_t *e = &host_files[(row_off + i) % host_count];
            lv_snprintf(line, sizeof line, "%c %-20s %u",
                        e->kind == OF_KIND_DIR ? '/' : ' ', e->name, (unsigned)e->size);
            lv_label_set_text(row_lbl[i], line);
        }
    }
}

static void ui_refresh(void)
{
    ui_rows();
    ui_status();
    lv_refr_now(NULL);
}

static void ui_create(void)
{
    if (panel_init())
        return;
    lv_init();
    lv_display_t *disp = lv_display_create(PANEL_W, PANEL_H);
    lv_display_set_color_format(disp, LV_COLOR_FORMAT_RGB565);
    static uint16_t buf[PANEL_W * 8];
    lv_display_set_buffers(disp, buf, 0, sizeof buf, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(disp, of_flush);

    lv_obj_t *scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x0b1016), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);

    lv_obj_t *title = lv_label_create(scr);
    lv_label_set_text(title, "OSCURO FILE BRIDGE");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(0x54c6ff), 0);
    lv_obj_set_pos(title, 4, 2);

    status_lbl = lv_label_create(scr);
    lv_obj_set_style_text_font(status_lbl, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(status_lbl, lv_color_hex(0x9fb0c0), 0);
    lv_obj_set_pos(status_lbl, 4, 20);

    for (unsigned i = 0; i < ROWS; i++) {
        row_lbl[i] = lv_label_create(scr);
        lv_obj_set_style_text_font(row_lbl[i], &lv_font_montserrat_14, 0);
        lv_obj_set_style_text_color(row_lbl[i], lv_color_hex(0xdfe8f0), 0);
        lv_obj_set_pos(row_lbl[i], 4, 42 + (int)i * 19);
    }

    ui_ready = true;
    ui_refresh();
}

void of_flush(lv_display_t *disp, const lv_area_t *a, uint8_t *buffer)
{
    panel_write(a->x1, a->y1, a->x2, a->y2, (const uint16_t *)buffer);
    lv_display_flush_ready(disp);
}

/* ---- protocol -------------------------------------------------------------- */

static bool of_send(uint8_t type, const uint8_t *p, unsigned len)
{
    if (len > OF_PAYLOAD_MAX || txq_n >= TXQ_SLOTS)
        return false;
    uint8_t *f = txq[txq_tail];
    f[0] = OF_MAGIC0;
    f[1] = OF_MAGIC1;
    f[2] = type;
    f[3] = 0;
    f[4] = (uint8_t)len;
    f[5] = 0;
    f[6] = 0;
    f[7] = 0;
    if (len)
        memcpy(f + OF_HDR, p, len);
    txq_len[txq_tail] = (uint8_t)(len + OF_HDR);
    txq_tail = (txq_tail + 1u) % TXQ_SLOTS;
    txq_n++;
    return true;
}

static void of_send_entry(const char *name, uint32_t size, uint8_t kind)
{
    uint8_t e[OF_PAYLOAD_MAX];
    unsigned n = (unsigned)strlen(name);
    if (n > OF_NAME_MAX)
        n = OF_NAME_MAX;
    e[0] = (uint8_t)n;
    memcpy(e + 1, name, n);
    e[1 + n] = (uint8_t)size;
    e[2 + n] = (uint8_t)(size >> 8);
    e[3 + n] = (uint8_t)(size >> 16);
    e[4 + n] = (uint8_t)(size >> 24);
    e[5 + n] = kind;
    of_send(OF_MSG_LIST_ENTRY, e, 6 + n);
}

static void of_serve_list(void)
{
    uint8_t cnt[2] = { (uint8_t)DEV_FILES_N, (uint8_t)(DEV_FILES_N >> 8) };
    of_send(OF_MSG_LIST_BEGIN, cnt, 2);
    for (unsigned i = 0; i < DEV_FILES_N; i++)
        of_send_entry(dev_files[i].name, dev_files[i].size, OF_KIND_FILE);
    of_send(OF_MSG_LIST_END, cnt, 2);
    served_ok = (uint32_t)DEV_FILES_N;
    ui_status();
}

static void of_add_entry(const uint8_t *p, unsigned len)
{
    if (len < 6 || host_count >= OF_FILES_MAX)
        return;
    unsigned n = p[0];
    if (n > OF_NAME_MAX || 6u + n > len || n == 0)
        return;
    of_entry_t *e = &host_files[host_count];
    memcpy(e->name, p + 1, n);
    e->name[n] = 0;
    e->size = (uint32_t)p[1 + n] | (uint32_t)p[2 + n] << 8 |
              (uint32_t)p[3 + n] << 16 | (uint32_t)p[4 + n] << 24;
    e->kind = p[5 + n];
    host_count++;
}

static void of_on_frame(const uint8_t *f, unsigned n)
{
    if (n < OF_HDR || f[0] != OF_MAGIC0 || f[1] != OF_MAGIC1)
        return;
    unsigned len = (unsigned)f[4] | (unsigned)f[5] << 8;
    if (len > OF_PAYLOAD_MAX || OF_HDR + len > n)
        return;
    const uint8_t *p = f + OF_HDR;
    switch (f[2]) {
    case OF_MSG_HELLO: {
        unsigned k = len < sizeof peer_name - 1 ? len : sizeof peer_name - 1;
        if (k) {
            memcpy(peer_name, p, k);
            peer_name[k] = 0;
            have_peer = true;
            ui_status();
        }
        break;
    }
    case OF_MSG_LIST_REQ:
        of_serve_list();
        break;
    case OF_MSG_LIST_BEGIN:
        assembling = true;
        host_count = 0;
        break;
    case OF_MSG_LIST_ENTRY:
        if (assembling)
            of_add_entry(p, len);
        break;
    case OF_MSG_LIST_END:
        assembling = false;
        row_off = 0;
        ui_refresh();
        {
            uint8_t ack = OF_MSG_LIST_END;
            of_send(OF_MSG_ACK, &ack, 1);
        }
        break;
    case OF_MSG_PING:
        of_send(OF_MSG_PONG, 0, 0);
        break;
    default:
        break;
    }
}

static void of_poll_usb(void)
{
    bool cfg = usbven_configured();
    if (cfg && !was_configured) {
        was_configured = true;
        txq_head = txq_tail = txq_n = 0;
        const char *hello = "SMK-37 OSCURO";
        of_send(OF_MSG_HELLO, (const uint8_t *)hello, (unsigned)strlen(hello));
        of_send(OF_MSG_LIST_REQ, 0, 0);              /* ask the host for its list */
    } else if (!cfg && was_configured) {
        was_configured = false;
        have_peer = false;
        host_count = 0;
        ui_refresh();
    }

    while (txq_n && usbven_tx_ready()) {
        usbven_tx(txq[txq_head], txq_len[txq_head]);
        txq_head = (txq_head + 1u) % TXQ_SLOTS;
        txq_n--;
    }
    uint8_t frame[OF_PKT_MAX];
    unsigned r = usbven_rx(frame);
    if (r)
        of_on_frame(frame, r);
}

/* ---- main ------------------------------------------------------------------ */

extern unsigned char __bss_start[], __bss_end[];

__attribute__((section(".text.entry"), used)) int entry(unsigned arg)
{
    (void)arg;
    for (unsigned char *p = __bss_start; p < __bss_end; ++p)
        *p = 0;

    ui_create();
    /* UBOOT's own USB interrupt handler must never run once the port is reset
     * under it (the Oscuro live payload does the same). */
    __asm__ volatile("cli");
    usbven_start(240u, 100u);

    uint32_t iter = 0;
    for (;;) {
        usbven_poll();
        of_poll_usb();
        if (ui_ready && host_count > ROWS && ++iter % 40000u == 0) {
            row_off = (row_off + 1u) % host_count;
            ui_rows();
            lv_refr_now(NULL);
        }
        wdt_clear();
    }
    return 0;
}
