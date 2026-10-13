// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Aleksei Tveritinov (Kartun)

package ai.oscuro.filebridge

import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** One entry of a fake file list. */
data class FileEntry(val name: String, val size: Long, val kind: Int) {
    val isDir get() = kind == Proto.KIND_DIR
    override fun toString() = (if (isDir) "/" else " ") + name + "   " + size
}

/**
 * Oscuro File Bridge wire protocol (see docs/proto/USB_FILE_BRIDGE.md).
 * One USB bulk packet carries exactly one message; both directions are
 * symmetric, so each side can serve a list and ask the other for its list.
 */
object Proto {
    const val VID = 0x1209
    const val PID = 0x0003
    const val PKT = 64
    const val HDR = 8
    const val PAYLOAD_MAX = PKT - HDR
    const val NAME_MAX = 40

    const val MSG_HELLO = 0x01
    const val MSG_LIST_BEGIN = 0x02
    const val MSG_LIST_ENTRY = 0x03
    const val MSG_LIST_END = 0x04
    const val MSG_LIST_REQ = 0x05
    const val MSG_ACK = 0x06
    const val MSG_PING = 0x07
    const val MSG_PONG = 0x08

    const val KIND_FILE = 0
    const val KIND_DIR = 1

    fun frame(type: Int, payload: ByteArray = ByteArray(0)): ByteArray {
        require(payload.size <= PAYLOAD_MAX)
        val b = ByteArray(HDR + payload.size)
        b[0] = 'O'.code.toByte()
        b[1] = 'F'.code.toByte()
        b[2] = type.toByte()
        b[3] = 0
        b[4] = (payload.size and 0xFF).toByte()
        b[5] = ((payload.size shr 8) and 0xFF).toByte()
        b[6] = 0
        b[7] = 0
        System.arraycopy(payload, 0, b, HDR, payload.size)
        return b
    }

    /** Returns (type, payload) or null when the packet is not a valid frame. */
    fun parse(buf: ByteArray, len: Int): Pair<Int, ByteArray>? {
        if (len < HDR || buf[0] != 'O'.code.toByte() || buf[1] != 'F'.code.toByte()) return null
        val n = (buf[4].toInt() and 0xFF) or ((buf[5].toInt() and 0xFF) shl 8)
        if (n > PAYLOAD_MAX || HDR + n > len) return null
        return Pair(buf[2].toInt() and 0xFF, buf.copyOfRange(HDR, HDR + n))
    }

    fun entryPayload(name: String, size: Long, kind: Int): ByteArray {
        val nb = name.toByteArray(Charsets.US_ASCII).take(NAME_MAX).toByteArray()
        val b = ByteArray(6 + nb.size)
        b[0] = nb.size.toByte()
        System.arraycopy(nb, 0, b, 1, nb.size)
        val sz = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(size.toInt()).array()
        System.arraycopy(sz, 0, b, 1 + nb.size, 4)
        b[5 + nb.size] = kind.toByte()
        return b
    }

    fun parseEntry(p: ByteArray): FileEntry? {
        if (p.size < 6) return null
        val n = p[0].toInt() and 0xFF
        if (n == 0 || 6 + n > p.size) return null
        val name = String(p, 1, n, Charsets.US_ASCII)
        val size = ByteBuffer.wrap(p, 1 + n, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong() and 0xFFFFFFFFL
        return FileEntry(name, size, p[5 + n].toInt() and 0xFF)
    }
}

/** An open bulk pipe to the keyboard. All calls are blocking; run off the UI thread. */
class FileBridge(
    private val conn: UsbDeviceConnection,
    private val epIn: UsbEndpoint,
    private val epOut: UsbEndpoint,
) {
    fun send(type: Int, payload: ByteArray = ByteArray(0)) {
        val f = Proto.frame(type, payload)
        conn.bulkTransfer(epOut, f, f.size, 1000)
    }

    fun sendList(files: List<FileEntry>) {
        send(Proto.MSG_LIST_BEGIN, byteArrayOf((files.size and 0xFF).toByte(), ((files.size shr 8) and 0xFF).toByte()))
        for (f in files) send(Proto.MSG_LIST_ENTRY, Proto.entryPayload(f.name, f.size, f.kind))
        send(Proto.MSG_LIST_END, byteArrayOf((files.size and 0xFF).toByte(), ((files.size shr 8) and 0xFF).toByte()))
    }

    /** One message, or null on timeout. */
    fun read(timeoutMs: Int = 300): Pair<Int, ByteArray>? {
        val buf = ByteArray(Proto.PKT)
        val n = conn.bulkTransfer(epIn, buf, buf.size, timeoutMs)
        if (n <= 0) return null
        return Proto.parse(buf, n)
    }

    fun close() {
        conn.close()
    }

    companion object {
        /**
         * Claim interface 0 of [device] and find its bulk endpoints. Returns null
         * when the device is not the File Bridge or has no bulk pipe.
         */
        fun open(manager: UsbManager, device: UsbDevice): FileBridge? {
            if (device.vendorId != Proto.VID || device.productId != Proto.PID) return null
            val conn = manager.openDevice(device) ?: return null
            val intf: UsbInterface = device.getInterface(0)
            var epIn: UsbEndpoint? = null
            var epOut: UsbEndpoint? = null
            for (i in 0 until intf.endpointCount) {
                val ep = intf.getEndpoint(i)
                if (ep.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
                if (ep.direction == UsbConstants.USB_DIR_IN) epIn = ep else epOut = ep
            }
            if (epIn == null || epOut == null) {
                conn.close()
                return null
            }
            if (!conn.claimInterface(intf, true)) {
                conn.close()
                return null
            }
            return FileBridge(conn, epIn, epOut!!)
        }
    }
}
