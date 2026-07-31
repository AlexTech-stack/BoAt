package com.boat.companion.trace

import java.io.BufferedOutputStream
import java.io.Closeable
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/* ── Link types (DLT) ────────────────────────────────────────────────── */

const val DLT_EN10MB = 1

/** Linux SocketCAN can_frame/canfd_frame. */
const val DLT_CAN_SOCKETCAN = 227

/* ── SocketCAN can_id flag bits ──────────────────────────────────────── */

/** 29-bit identifier. Wireshark reads extended-ness from this bit, not the id value. */
const val CAN_EFF_FLAG = 0x80000000.toInt()
const val CAN_RTR_FLAG = 0x40000000
const val CAN_ERR_FLAG = 0x20000000

/* ── CAN FD flags (canfd_frame.flags) ────────────────────────────────── */

const val CANFD_BRS = 0x01
const val CANFD_ESI = 0x02
const val CANFD_FDF = 0x04

private const val BT_SHB = 0x0A0D0D0A
private const val BT_IDB = 0x00000001
private const val BT_EPB = 0x00000006
private const val BYTE_ORDER_MAGIC = 0x1A2B3C4D

private const val OPT_ENDOFOPT = 0
private const val OPT_COMMENT = 1
private const val OPT_IF_NAME = 2
private const val OPT_SHB_USERAPPL = 4
private const val OPT_IF_TSRESOL = 9

/** if_tsresol exponent: timestamps are in nanoseconds (10^-9 s). */
private const val TSRESOL_NANOSECONDS: Byte = 9

private fun pad4(n: Int): Int = (4 - (n % 4)) % 4

/**
 * Writes CAN frames to a PCAPNG file readable by Wireshark and by BoAt's own
 * `boat.pcapng.PcapngReader`.
 *
 * This is a deliberate port of `sdk/python/boat/pcapng.py`'s writer: the byte
 * layout is matched block for block so traces recorded on a phone are the same
 * artefact the gateway produces, and `boat replay import` / `trace_analyzer`
 * accept them without a conversion step.
 *
 * Note the mixed endianness, which is not a mistake and is the usual source of
 * broken CAN pcapng files: PCAPNG blocks are little-endian (declared by the
 * byte-order magic), while the SocketCAN `can_id` inside each packet is
 * **big-endian** as DLT_CAN_SOCKETCAN requires. Writing the id little-endian
 * produces a file that parses cleanly and shows entirely wrong CAN IDs.
 */
class PcapngWriter(
    stream: OutputStream,
    /** Free-text provenance, stored as an SHB comment and visible in Wireshark. */
    comment: String? = null,
    application: String? = null,
) : Closeable {

    private val out = BufferedOutputStream(stream)
    private val lock = Any()
    private var closed = false
    private var interfaceCount = 0

    init {
        writeShb(comment, application)
    }

    /**
     * Registers an interface and returns its id. Must be called before writing
     * any frame that references it.
     */
    fun addInterface(name: String, linkType: Int = DLT_CAN_SOCKETCAN): Int {
        val options = option(OPT_IF_NAME, name.toByteArray(Charsets.UTF_8)) +
            option(OPT_IF_TSRESOL, byteArrayOf(TSRESOL_NANOSECONDS)) +
            option(OPT_ENDOFOPT, ByteArray(0))

        val header = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putShort(linkType.toShort())
            .putShort(0)          // reserved
            .putInt(65535)        // snaplen
            .array()

        synchronized(lock) {
            val id = interfaceCount++
            writeBlock(BT_IDB, header + options)
            return id
        }
    }

    /**
     * Appends one CAN or CAN FD frame.
     *
     * [timestampNanos] is nanoseconds since the Unix epoch. It is a Long rather
     * than a Double of seconds because a Double carries 53 bits of mantissa and
     * epoch nanoseconds need 61 — seconds-as-Double silently quantises to about
     * 250 ns, which would undercut the resolution the file claims.
     *
     * [canId] is the raw 11- or 29-bit identifier; set [extended] rather than
     * folding CAN_EFF_FLAG in yourself.
     */
    fun writeCan(
        interfaceId: Int,
        timestampNanos: Long,
        canId: Int,
        data: ByteArray,
        extended: Boolean = false,
        fd: Boolean = false,
        fdFlags: Int = 0,
    ) {
        var id = canId
        if (extended) id = id or CAN_EFF_FLAG
        writePacket(interfaceId, timestampNanos, packCanFrame(id, data, fd, fdFlags))
    }

    /** Appends a pre-encoded packet for [interfaceId]. */
    fun writePacket(interfaceId: Int, timestampNanos: Long, packet: ByteArray) {
        val body = ByteBuffer.allocate(20 + packet.size).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(interfaceId)
            .putInt((timestampNanos ushr 32).toInt())
            .putInt(timestampNanos.toInt())
            .putInt(packet.size)   // captured length
            .putInt(packet.size)   // original length
            .put(packet)
            .array()

        synchronized(lock) {
            check(!closed) { "writer is closed" }
            writeBlock(BT_EPB, body)
        }
    }

    fun flush() {
        synchronized(lock) { if (!closed) out.flush() }
    }

    /** Idempotent. */
    override fun close() {
        synchronized(lock) {
            if (closed) return
            closed = true
            runCatching { out.flush() }
            runCatching { out.close() }
        }
    }

    private fun writeShb(comment: String?, application: String?) {
        var options = ByteArray(0)
        if (comment != null) {
            options += option(OPT_COMMENT, comment.toByteArray(Charsets.UTF_8))
        }
        if (application != null) {
            options += option(OPT_SHB_USERAPPL, application.toByteArray(Charsets.UTF_8))
        }
        if (options.isNotEmpty()) options += option(OPT_ENDOFOPT, ByteArray(0))

        val header = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(BYTE_ORDER_MAGIC)
            .putShort(1)      // version major
            .putShort(0)      // version minor
            .putLong(-1L)     // section length: unknown
            .array()

        synchronized(lock) { writeBlock(BT_SHB, header + options) }
    }

    /** total_length appears twice, before and after the body, per the spec. */
    private fun writeBlock(blockType: Int, body: ByteArray) {
        val padding = pad4(body.size)
        val totalLength = 12 + body.size + padding
        val buffer = ByteBuffer.allocate(totalLength).order(ByteOrder.LITTLE_ENDIAN)
        buffer.putInt(blockType)
        buffer.putInt(totalLength)
        buffer.put(body)
        repeat(padding) { buffer.put(0) }
        buffer.putInt(totalLength)
        out.write(buffer.array())
    }

    private fun option(code: Int, value: ByteArray): ByteArray {
        val padding = pad4(value.size)
        val buffer = ByteBuffer.allocate(4 + value.size + padding).order(ByteOrder.LITTLE_ENDIAN)
        buffer.putShort(code.toShort())
        buffer.putShort(value.size.toShort())
        buffer.put(value)
        repeat(padding) { buffer.put(0) }
        return buffer.array()
    }

    companion object {
        /**
         * Encodes a SocketCAN `can_frame` (16 bytes) or `canfd_frame` (72 bytes).
         *
         * Readers tell the two apart by total length, which is also how Wireshark
         * does it — so the payload area is always padded to its full 8 or 64
         * bytes rather than truncated to the actual data length.
         */
        fun packCanFrame(
            canId: Int,
            data: ByteArray,
            fd: Boolean,
            fdFlags: Int = 0,
        ): ByteArray {
            val payloadCapacity = if (fd) 64 else 8
            val length = minOf(data.size, payloadCapacity)
            // allocate() zero-fills, which supplies the padding for free.
            val buffer = ByteBuffer.allocate(8 + payloadCapacity).order(ByteOrder.BIG_ENDIAN)
            buffer.putInt(canId)
            buffer.put(length.toByte())
            // The flags byte only exists on canfd_frame; on can_frame it is padding.
            buffer.put(if (fd) fdFlags.toByte() else 0)
            buffer.put(0)
            buffer.put(0)
            buffer.put(data, 0, length)
            return buffer.array()
        }
    }
}
