package com.boat.companion.usb

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.Closeable
import kotlin.coroutines.resume

private const val ACTION_USB_PERMISSION = "com.boat.companion.USB_PERMISSION"

/**
 * A WeAct USB2CANFDV2 attached over USB-OTG, driven in SLCAN mode.
 *
 * The adapter has no hardware acceptance filters, so every frame on the bus
 * crosses USB and any filtering happens here. Measured on a desktop host, it
 * sustained a saturated 500 kbit bus (~4300 frames/s) with zero loss; the phone
 * has less headroom, which is why the read loop does nothing but buffer bytes
 * and hand them to the assembler.
 */
class SlcanAdapter(private val connection: CdcAcmConnection) : Closeable {

    private val assembler = SlcanFrameAssembler()

    /**
     * Configures and opens the CAN channel.
     *
     * Order matters: bitrate and mode are only settable while the channel is
     * closed, so an explicit close comes first even on a freshly attached device
     * whose state is unknown.
     */
    fun open(
        bitrate: SlcanCodec.Bitrate = SlcanCodec.Bitrate.B500K,
        silent: Boolean = false,
    ): Result<Unit> {
        val steps = listOf(
            "close" to SlcanCodec.close(),
            "bitrate" to SlcanCodec.bitrate(bitrate),
            "mode" to SlcanCodec.mode(silent),
            "open" to SlcanCodec.open(),
        )
        for ((name, command) in steps) {
            val written = connection.write(command)
            if (written < 0) {
                return Result.failure(IllegalStateException("USB write failed during '$name'"))
            }
            // Drain the acknowledgement so it cannot be mistaken for frame data.
            val scratch = ByteArray(64)
            connection.read(scratch, timeoutMs = 300)
        }
        assembler.reset()
        return Result.success(Unit)
    }

    /** Transmits a frame onto the bus. */
    fun send(frame: SlcanFrame): Result<Unit> {
        val written = connection.write(SlcanCodec.encode(frame))
        return if (written < 0) Result.failure(IllegalStateException("USB write failed"))
        else Result.success(Unit)
    }

    /**
     * Reads available bytes and returns any complete frames.
     *
     * Timestamps are taken here, at the moment the bytes surface from USB. The
     * firmware provides none, so these carry USB polling and scheduler jitter —
     * see the note recorded in each PCAPNG section header.
     */
    fun poll(): List<SlcanFrame> {
        val buffer = ByteArray(CdcAcmConnection.READ_BUFFER_SIZE)
        val count = connection.read(buffer)
        if (count <= 0) return emptyList()
        val now = System.currentTimeMillis() * 1_000_000L
        return assembler.append(buffer, count).mapNotNull { record ->
            SlcanCodec.decode(record)?.copy(timestampNanos = now)
        }
    }

    override fun close() {
        runCatching { connection.write(SlcanCodec.close()) }
        connection.close()
    }
}

/** Attachment and permission handling for the USB adapter. */
object UsbAdapterHost {

    fun findAdapter(context: Context): UsbDevice? =
        CdcAcmConnection.find(context.getSystemService(Context.USB_SERVICE) as UsbManager)

    fun hasPermission(context: Context, device: UsbDevice): Boolean =
        (context.getSystemService(Context.USB_SERVICE) as UsbManager).hasPermission(device)

    /**
     * Requests access, suspending until the user answers the system dialog.
     *
     * The PendingIntent must be immutable: Android 12+ rejects mutable implicit
     * PendingIntents outright, and this app targets well past that.
     */
    suspend fun requestPermission(context: Context, device: UsbDevice): Boolean {
        val manager = context.getSystemService(Context.USB_SERVICE) as UsbManager
        if (manager.hasPermission(device)) return true

        return suspendCancellableCoroutine { continuation ->
            val receiver = object : BroadcastReceiver() {
                override fun onReceive(receiverContext: Context, intent: Intent) {
                    if (intent.action != ACTION_USB_PERMISSION) return
                    runCatching { context.unregisterReceiver(this) }
                    val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
                    if (continuation.isActive) continuation.resume(granted)
                }
            }
            ContextCompat_registerReceiver(context, receiver, IntentFilter(ACTION_USB_PERMISSION))

            val pendingIntent = PendingIntent.getBroadcast(
                context, 0, Intent(ACTION_USB_PERMISSION).setPackage(context.packageName),
                PendingIntent.FLAG_IMMUTABLE,
            )
            manager.requestPermission(device, pendingIntent)

            continuation.invokeOnCancellation {
                runCatching { context.unregisterReceiver(receiver) }
            }
        }
    }

    fun connect(context: Context, device: UsbDevice): Result<SlcanAdapter> {
        val manager = context.getSystemService(Context.USB_SERVICE) as UsbManager
        return CdcAcmConnection.open(manager, device).map { SlcanAdapter(it) }
    }
}

/** RECEIVER_NOT_EXPORTED is mandatory from API 34 for non-system broadcasts. */
private fun ContextCompat_registerReceiver(
    context: Context,
    receiver: BroadcastReceiver,
    filter: IntentFilter,
) {
    if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
        context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
    } else {
        @Suppress("UnspecifiedRegisterReceiverFlag")
        context.registerReceiver(receiver, filter)
    }
}

/** Live frames from [adapter], polled on the calling coroutine's dispatcher. */
fun SlcanAdapter.frames(): Flow<SlcanFrame> = callbackFlow {
    val thread = Thread {
        try {
            while (!Thread.currentThread().isInterrupted) {
                for (frame in poll()) {
                    if (trySend(frame).isFailure) return@Thread
                }
            }
        } catch (_: InterruptedException) {
            // normal shutdown
        }
        close()
    }
    thread.isDaemon = true
    thread.start()
    awaitClose { thread.interrupt() }
}
