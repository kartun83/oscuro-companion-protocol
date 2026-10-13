/* Polled USB vendor-class device on the WL82 full-speed controller, for the
 * Oscuro File Bridge prototype. See usbven.c. */
#ifndef USBVEN_H
#define USBVEN_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t resets, setups, stalls, rx_packets, tx_packets, tx_busy;
    uint8_t address, configured, last_req, last_type;
    uint8_t stage;               /* 1 detached, 2 attached (pull-up on), 3 bus reset seen */
} usbven_stats_t;

extern usbven_stats_t usbven_stats;

/* Detach whatever owned the port (UBOOT's mass storage), wait detach_ms, then
 * attach as the Oscuro vendor device. Polled only: no interrupts are taken. */
void usbven_start(uint32_t cpu_mhz, uint32_t detach_ms);

/* Call often from the main loop: bus reset and EP0 requests. */
void usbven_poll(void);

/* True once the host has sent SET_CONFIGURATION and the bulk endpoints are up. */
bool usbven_configured(void);

/* EP1 IN: true when the previous packet has left and a new one may be sent. */
bool usbven_tx_ready(void);
void usbven_tx(const uint8_t *data, unsigned len);   /* len <= 64 */

/* EP1 OUT: copies one received packet into buf (64 bytes), returns its length,
 * 0 if none is waiting. */
unsigned usbven_rx(uint8_t *buf);

#endif
