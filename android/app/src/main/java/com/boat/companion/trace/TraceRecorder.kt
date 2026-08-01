package com.boat.companion.trace

import android.content.Context
import com.boat.companion.usb.SlcanFrame
import java.io.Closeable
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Bytes are flushed at least this often so a crash costs at most this many frames. */
private const val FLUSH_EVERY_FRAMES = 100

/**
 * Records frames from the USB adapter into a PCAPNG file.
 *
 * Files land in the app's external files directory, which needs no runtime
 * permission and survives uninstall-free upgrades. That directory is reachable
 * over adb and, via the share sheet, exportable anywhere — the recording is
 * useless if it is trapped on the device.
 */
class TraceRecorder private constructor(
    val file: File,
    private val writer: PcapngWriter,
    private val interfaceId: Int,
) : Closeable {

    var frameCount: Long = 0
        private set

    fun write(frame: SlcanFrame) {
        writer.writeCan(
            interfaceId = interfaceId,
            timestampNanos = frame.timestampNanos,
            canId = frame.id,
            data = frame.data,
            extended = frame.extended,
            fd = frame.fd,
            // FDF marks the frame as CAN FD for readers; BRS records that the
            // data phase actually switched rate, which Wireshark displays.
            fdFlags = if (frame.fd) {
                CANFD_FDF or (if (frame.brs) CANFD_BRS else 0)
            } else 0,
        )
        frameCount++
        if (frameCount % FLUSH_EVERY_FRAMES == 0L) writer.flush()
    }

    val sizeBytes: Long get() = file.length()

    override fun close() {
        writer.close()
    }

    companion object {
        private const val INTERFACE_NAME = "can0"

        fun directory(context: Context): File =
            File(context.getExternalFilesDir(null), "traces").apply { mkdirs() }

        fun list(context: Context): List<File> =
            directory(context).listFiles { f -> f.extension == "pcapng" }
                ?.sortedByDescending { it.lastModified() }
                ?: emptyList()

        /**
         * Opens a new recording.
         *
         * The timestamp caveat goes in the section header rather than on each
         * frame: provenance belongs to the capture session, and a trace file
         * outlives the session that made it — someone will eventually measure
         * inter-frame gaps from this and deserves to know what they are reading.
         */
        fun start(context: Context, bitrateBitsPerSecond: Int): Result<TraceRecorder> = runCatching {
            val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
            val file = File(directory(context), "boat-can-$stamp.pcapng")

            val writer = PcapngWriter(
                file.outputStream(),
                comment = "CAN capture at ${bitrateBitsPerSecond / 1000} kbit/s. " +
                    "Timestamps are host-side capture times taken when bytes surfaced " +
                    "from USB: the SLCAN adapter firmware provides no hardware " +
                    "timestamps, so inter-frame gaps carry USB and scheduler jitter.",
                application = "BoAt companion (Android)",
            )
            val interfaceId = writer.addInterface(INTERFACE_NAME, DLT_CAN_SOCKETCAN)
            TraceRecorder(file, writer, interfaceId)
        }
    }
}
