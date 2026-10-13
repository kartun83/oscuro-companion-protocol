/* SPDX-License-Identifier: Apache-2.0 */
/* Copyright 2026 Aleksei Tveritinov */

/* Oscuro File Bridge — the wire protocol shared by the keyboard RAM app
 * (usbfiles.c) and the host side (tools/usbfiles_host.py, android/).
 *
 * It is a deliberately tiny, self-describing, one-packet-per-message protocol
 * on the vendor bulk pipe. There is no state machine beyond "who is serving a
 * list right now": either side may ask the other for its list (LIST_REQ) or
 * volunteer its own (LIST_BEGIN … LIST_END). Both directions are symmetric, so
 * the same code runs on the device and on the host.
 *
 * Framing (little-endian), one USB bulk packet, at most OF_PKT_MAX bytes:
 *
 *   +0  u8  magic0   0x4F 'O'
 *   +1  u8  magic1   0x46 'F'
 *   +2  u8  type     OF_MSG_*
 *   +3  u8  flags    reserved, 0
 *   +4  u16 len      payload length (0..OF_PAYLOAD_MAX)
 *   +6  u8  seq      reserved, 0
 *   +7  u8  reserved 0
 *   +8  ..           payload
 *
 * A sender that needs more than one packet splits at the message level (one
 * LIST_ENTRY per file), never inside a message. The receiver parses exactly one
 * message per packet; a packet with a bad magic or an over-long len is dropped.
 */
#ifndef USBFILES_PROTO_H
#define USBFILES_PROTO_H

#include <stdint.h>

#define OF_MAGIC0 0x4F           /* 'O' */
#define OF_MAGIC1 0x46           /* 'F' */
#define OF_HDR 8
#define OF_PKT_MAX 64
#define OF_PAYLOAD_MAX (OF_PKT_MAX - OF_HDR)   /* 56 */
#define OF_NAME_MAX 40
#define OF_FILES_MAX 32

enum {
    OF_MSG_HELLO = 0x01,         /* payload: ascii name of the sender */
    OF_MSG_LIST_BEGIN = 0x02,    /* payload: u16 count of entries that follow */
    OF_MSG_LIST_ENTRY = 0x03,    /* payload: u8 namelen, name, u32 size, u8 kind */
    OF_MSG_LIST_END = 0x04,      /* payload: u16 count actually sent */
    OF_MSG_LIST_REQ = 0x05,      /* payload: empty; "send me your list" */
    OF_MSG_ACK = 0x06,           /* payload: u8 type being acknowledged */
    OF_MSG_PING = 0x07,          /* payload: empty */
    OF_MSG_PONG = 0x08,          /* payload: empty */
};

enum {
    OF_KIND_FILE = 0,
    OF_KIND_DIR = 1,
};

#endif
