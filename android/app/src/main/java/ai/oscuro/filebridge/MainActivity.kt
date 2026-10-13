// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Aleksei Tveritinov

package ai.oscuro.filebridge

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView

/**
 * Minimal Android USB-host app for the Oscuro File Bridge prototype.
 *
 * The keyboard is the USB device; this app is the host. It exposes a fake file
 * list to the keyboard (which shows it in its scroll window) and asks the
 * keyboard for its own fake list, printing both.
 */
class MainActivity : Activity() {

    private lateinit var usb: UsbManager
    private var bridge: FileBridge? = null
    private var reader: Thread? = null
    @Volatile private var running = false

    private lateinit var status: TextView
    private lateinit var hostAdapter: ArrayAdapter<String>
    private lateinit var deviceAdapter: ArrayAdapter<String>

    private val hostFiles = mutableListOf(
        FileEntry("phone_notes.txt", 1200, Proto.KIND_FILE),
        FileEntry("recording_01.wav", 4_200_000, Proto.KIND_FILE),
        FileEntry("presets_backup.bak", 88_000, Proto.KIND_FILE),
        FileEntry("setlist_a.m3u", 320, Proto.KIND_FILE),
        FileEntry("demo.mp3", 3_100_000, Proto.KIND_FILE),
        FileEntry("inbox", 0, Proto.KIND_DIR),
        FileEntry("exports", 0, Proto.KIND_DIR),
    )

    private val permissionAction = "ai.oscuro.filebridge.USB_PERMISSION"
    private val handler = Handler(Looper.getMainLooper())

    private val permissionReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != permissionAction) return
            val device = deviceFromIntent(intent)
            if (intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false) && device != null) {
                connect(device)
            } else {
                setStatus("USB permission denied")
            }
        }
    }

    private fun deviceFromIntent(intent: Intent?): UsbDevice? =
        if (Build.VERSION.SDK_INT >= 33) {
            intent?.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent?.getParcelableExtra(UsbManager.EXTRA_DEVICE)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        usb = getSystemService(Context.USB_SERVICE) as UsbManager
        buildUi()
        val filter = IntentFilter(permissionAction)
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(permissionReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(permissionReceiver, filter)
        }
        val fromIntent = deviceFromIntent(intent)
        if (fromIntent != null) maybeConnect(fromIntent) else scanAndConnect()
    }

    override fun onDestroy() {
        running = false
        try { unregisterReceiver(permissionReceiver) } catch (_: Exception) {}
        bridge?.close()
        super.onDestroy()
    }

    private fun buildUi() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 24, 24, 24)
        }
        val title = TextView(this).apply {
            text = "Oscuro File Bridge"
            textSize = 20f
        }
        status = TextView(this).apply { text = "Looking for the keyboard…" }
        root.addView(title)
        root.addView(status)

        root.addView(TextView(this).apply { text = "\nThis phone exposes (host → keyboard):" })
        hostAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, hostFiles.map { it.toString() }.toMutableList())
        root.addView(ListView(this).apply {
            adapter = hostAdapter
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f)
        })

        root.addView(TextView(this).apply { text = "Keyboard serves (keyboard → host):" })
        deviceAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, mutableListOf<String>())
        root.addView(ListView(this).apply {
            adapter = deviceAdapter
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f)
        })

        val buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
        }
        buttons.addView(Button(this).apply {
            text = "Push list"
            setOnClickListener { pushList() }
        })
        buttons.addView(Button(this).apply {
            text = "Ask keyboard"
            setOnClickListener { askKeyboard() }
        })
        root.addView(buttons, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
        setContentView(root)
        hostAdapter.notifyDataSetChanged()
    }

    private fun setStatus(text: String) = handler.post { status.text = text }

    private fun scanAndConnect() {
        val device = usb.deviceList.values.firstOrNull { it.vendorId == Proto.VID && it.productId == Proto.PID }
        if (device == null) {
            setStatus("Keyboard not found. Run the payload, plug it into this phone (OTG), then tap Reconnect via relaunch.")
            return
        }
        maybeConnect(device)
    }

    private fun maybeConnect(device: UsbDevice) {
        if (usb.hasPermission(device)) {
            connect(device)
        } else {
            val flags = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_IMMUTABLE else 0
            val pi = PendingIntent.getBroadcast(this, 0, Intent(permissionAction).setPackage(packageName), flags)
            usb.requestPermission(device, pi)
            setStatus("Waiting for USB permission…")
        }
    }

    private fun connect(device: UsbDevice) {
        setStatus("Connecting to ${device.deviceName}…")
        Thread {
            val b = FileBridge.open(usb, device)
            if (b == null) {
                setStatus("Could not open the vendor interface")
                return@Thread
            }
            bridge?.close()
            bridge = b
            running = true
            b.send(Proto.MSG_HELLO, "ANDROID HOST".toByteArray(Charsets.US_ASCII))
            b.sendList(hostFiles)
            b.send(Proto.MSG_LIST_REQ)
            setStatus("Connected. Served ${hostFiles.size} files; asked the keyboard for its list.")
            readLoop(b)
        }.start()
    }

    private fun readLoop(b: FileBridge) {
        reader = Thread {
            var collecting = false
            val got = mutableListOf<String>()
            while (running) {
                val msg = b.read(300) ?: continue
                val (type, payload) = msg
                when (type) {
                    Proto.MSG_HELLO -> setStatus("Connected to: ${String(payload, Charsets.US_ASCII)}")
                    Proto.MSG_LIST_BEGIN -> { collecting = true; got.clear() }
                    Proto.MSG_LIST_ENTRY -> if (collecting) Proto.parseEntry(payload)?.let { got.add(it.toString()) }
                    Proto.MSG_LIST_END -> {
                        collecting = false
                        b.send(Proto.MSG_ACK, byteArrayOf(Proto.MSG_LIST_END.toByte()))
                        handler.post {
                            deviceAdapter.clear()
                            deviceAdapter.addAll(got)
                            deviceAdapter.notifyDataSetChanged()
                            status.text = "Keyboard serves ${got.size} files."
                        }
                    }
                    Proto.MSG_LIST_REQ -> Thread { bridge?.sendList(hostFiles) }.start()
                    Proto.MSG_PING -> b.send(Proto.MSG_PONG)
                }
            }
        }
    }

    private fun pushList() {
        Thread { bridge?.sendList(hostFiles) }.start()
    }

    private fun askKeyboard() {
        Thread { bridge?.send(Proto.MSG_LIST_REQ) }.start()
    }
}
