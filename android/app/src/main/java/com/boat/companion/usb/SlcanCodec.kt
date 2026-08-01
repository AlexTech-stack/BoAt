package com.boat.companion.usb

/**
 * SLCAN (Lawicel ASCII) encoding for the WeAct USB2CANFDV2.
 *
 * The command set was read off the device rather than taken from its
 * documentation, which describes transmit commands only:
 *
 *   supported : C  O  S0-SC  Sxxyy  Y1-Y5  Yxxyy  M0/M1  V
 *   absent    : Z (timestamps)  F (status flags)  v  N  and any acceptance filter
 *
 * Received frames therefore carry no timestamp suffix, and there is no hardware
 * filtering — every frame on the bus arrives here and must be filtered in
 * software. `V` answers with a free-text string ("WeAct Studio V1.0.0.0")
 * instead of the standard `Vhhss`, so it is not useful for version detection.
 */
object SlcanCodec {

    const val CR: Byte = 0x0D
    private const val BEL: Byte = 0x07

    /** Acknowledgement bytes the adapter replies with. */
    val ACK = byteArrayOf(CR)
    val NACK = byteArrayOf(BEL)

    /* ── Commands ────────────────────────────────────────────────────── */

    fun open(): ByteArray = "O\r".toByteArray()

    fun close(): ByteArray = "C\r".toByteArray()

    /** M0 = normal (acknowledges frames), M1 = silent/listen-only. */
    fun mode(silent: Boolean): ByteArray = if (silent) "M1\r".toByteArray() else "M0\r".toByteArray()

    fun version(): ByteArray = "V\r".toByteArray()

    /** Arbitration bitrate. Only settable while the channel is closed. */
    fun bitrate(preset: Bitrate): ByteArray = "S${preset.code}\r".toByteArray()

    /** CAN FD data-phase bitrate. Only settable while the channel is closed. */
    fun dataBitrate(preset: DataBitrate): ByteArray = "Y${preset.code}\r".toByteArray()

    enum class Bitrate(val code: Char, val bitsPerSecond: Int) {
        B10K('0', 10_000),
        B20K('1', 20_000),
        B50K('2', 50_000),
        B100K('3', 100_000),
        B125K('4', 125_000),
        B250K('5', 250_000),
        B500K('6', 500_000),
        B800K('7', 800_000),
        B1M('8', 1_000_000),
        ;

        companion object {
            fun of(bitsPerSecond: Int): Bitrate? = entries.firstOrNull {
                it.bitsPerSecond == bitsPerSecond
            }
        }
    }

    enum class DataBitrate(val code: Char, val bitsPerSecond: Int) {
        D1M('1', 1_000_000),
        D2M('2', 2_000_000),
        D3M('3', 3_000_000),
        D4M('4', 4_000_000),
        D5M('5', 5_000_000),
    }

    /**
     * Encodes a frame for transmission.
     *
     * Lowercase is an 11-bit identifier, uppercase 29-bit: `t`/`T` classic,
     * `d`/`D` CAN FD at the arbitration rate, `b`/`B` CAN FD with bitrate
     * switching. Treating `b`/`B` as unknown silently drops every BRS frame,
     * which is most FD traffic in practice.
     */
    fun encode(frame: SlcanFrame): ByteArray {
        val builder = StringBuilder()
        // Bitrate switching gets its own letter pair: d/D carry FD frames that
        // stay at the arbitration rate, b/B carry those that switch.
        val letter = when {
            frame.fd && frame.brs && frame.extended -> 'B'
            frame.fd && frame.brs -> 'b'
            frame.fd && frame.extended -> 'D'
            frame.fd -> 'd'
            frame.extended -> 'T'
            else -> 't'
        }
        builder.append(letter)
        builder.append(
            if (frame.extended) "%08X".format(frame.id and 0x1FFFFFFF)
            else "%03X".format(frame.id and 0x7FF)
        )
        builder.append("%X".format(lengthToDlcCode(frame.data.size, frame.fd)))
        for (byte in frame.data) builder.append("%02X".format(byte))
        builder.append('\r')
        return builder.toString().toByteArray(Charsets.US_ASCII)
    }

    /* ── Receive ─────────────────────────────────────────────────────── */

    /**
     * Parses one CR-delimited record. Returns null for anything that is not a
     * frame — acknowledgements, error bytes and the free-text `V` reply all
     * share the stream and are not failures.
     */
    fun decode(record: ByteArray): SlcanFrame? {
        if (record.isEmpty()) return null
        val text = String(record, Charsets.US_ASCII)
        val letter = text[0]

        val extended = letter == 'T' || letter == 'D' || letter == 'B'
        val fd = letter in "dDbB"
        val brs = letter == 'b' || letter == 'B'
        if (letter !in "tTdDbB") return null

        val idDigits = if (extended) 8 else 3
        // letter + id + one length nibble
        if (text.length < 1 + idDigits + 1) return null

        val id = text.substring(1, 1 + idDigits).toIntOrNull(16) ?: return null
        val dlcCode = text.substring(1 + idDigits, 2 + idDigits).toIntOrNull(16) ?: return null
        val length = dlcCodeToLength(dlcCode, fd)

        val payloadText = text.substring(2 + idDigits)
        // A truncated record means the reassembler handed over a partial line.
        if (payloadText.length < length * 2) return null

        val data = ByteArray(length)
        for (i in 0 until length) {
            val byte = payloadText.substring(i * 2, i * 2 + 2).toIntOrNull(16) ?: return null
            data[i] = byte.toByte()
        }
        return SlcanFrame(id = id, data = data, extended = extended, fd = fd, brs = brs)
    }

    /**
     * CAN FD encodes lengths above 8 as a DLC code, since only 16 values are
     * available for up to 64 bytes. Classic CAN maps length to itself.
     */
    private fun dlcCodeToLength(code: Int, fd: Boolean): Int {
        if (!fd) return code.coerceAtMost(8)
        return when (code) {
            in 0..8 -> code
            9 -> 12
            10 -> 16
            11 -> 20
            12 -> 24
            13 -> 32
            14 -> 48
            15 -> 64
            else -> 0
        }
    }

    private fun lengthToDlcCode(length: Int, fd: Boolean): Int {
        if (!fd) return length.coerceAtMost(8)
        return when {
            length <= 8 -> length
            length <= 12 -> 9
            length <= 16 -> 10
            length <= 20 -> 11
            length <= 24 -> 12
            length <= 32 -> 13
            length <= 48 -> 14
            else -> 15
        }
    }
}

data class SlcanFrame(
    val id: Int,
    val data: ByteArray,
    val extended: Boolean = false,
    val fd: Boolean = false,
    /** Bitrate switch: the data phase ran at the FD data rate. */
    val brs: Boolean = false,
    /**
     * Host-side capture time. The firmware supplies no timestamps, so this is
     * assigned when the bytes are read and carries USB and scheduler jitter.
     */
    val timestampNanos: Long = 0,
) {
    // Generated equals/hashCode would compare the ByteArray by reference.
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is SlcanFrame) return false
        return id == other.id && extended == other.extended && fd == other.fd &&
            brs == other.brs && data.contentEquals(other.data)
    }

    override fun hashCode(): Int {
        var result = id
        result = 31 * result + data.contentHashCode()
        result = 31 * result + extended.hashCode()
        result = 31 * result + fd.hashCode()
        result = 31 * result + brs.hashCode()
        return result
    }
}

/**
 * Splits a byte stream into CR-delimited records.
 *
 * USB reads land on arbitrary boundaries — at bus saturation a single 64-byte
 * bulk packet holds roughly three frames and will routinely cut the last one in
 * half — so partial records must survive until their remainder arrives.
 */
class SlcanFrameAssembler(private val maxRecordLength: Int = 256) {

    private val buffer = StringBuilder()

    fun append(bytes: ByteArray, length: Int = bytes.size): List<ByteArray> {
        val records = mutableListOf<ByteArray>()
        for (i in 0 until length) {
            val byte = bytes[i]
            if (byte == SlcanCodec.CR) {
                if (buffer.isNotEmpty()) {
                    records.add(buffer.toString().toByteArray(Charsets.US_ASCII))
                    buffer.setLength(0)
                }
            } else {
                // Guard against a desynchronised stream growing without bound.
                if (buffer.length >= maxRecordLength) buffer.setLength(0)
                buffer.append(byte.toInt().toChar())
            }
        }
        return records
    }

    fun reset() = buffer.setLength(0)
}
