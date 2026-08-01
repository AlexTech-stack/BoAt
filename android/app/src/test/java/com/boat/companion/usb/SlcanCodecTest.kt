package com.boat.companion.usb

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The decode cases are the exact bytes captured from the adapter on a live
 * 500 kbit bus, not invented examples — the firmware's receive format is
 * undocumented, so these strings are the specification.
 */
class SlcanCodecTest {

    @Test
    fun `decodes a standard frame captured from the bus`() {
        val frame = SlcanCodec.decode("t1234DEADBEEF".toByteArray())!!
        assertEquals(0x123, frame.id)
        assertEquals(false, frame.extended)
        assertEquals(false, frame.fd)
        assertArrayEquals(
            byteArrayOf(0xDE.toByte(), 0xAD.toByte(), 0xBE.toByte(), 0xEF.toByte()),
            frame.data,
        )
    }

    @Test
    fun `decodes a single-byte frame`() {
        val frame = SlcanCodec.decode("t7FF101".toByteArray())!!
        assertEquals(0x7FF, frame.id)
        assertArrayEquals(byteArrayOf(0x01), frame.data)
    }

    @Test
    fun `decodes an extended frame`() {
        val frame = SlcanCodec.decode("T18DAF1104AABBCCDD".toByteArray())!!
        assertEquals(0x18DAF110, frame.id)
        assertTrue(frame.extended)
        assertArrayEquals(
            byteArrayOf(0xAA.toByte(), 0xBB.toByte(), 0xCC.toByte(), 0xDD.toByte()),
            frame.data,
        )
    }

    @Test
    fun `round-trips through encode and decode`() {
        val original = SlcanFrame(id = 0x321, data = byteArrayOf(0x11, 0x22, 0x33))
        val encoded = SlcanCodec.encode(original)
        // Matches the command that was verified on the wire.
        assertEquals("t3213112233\r", String(encoded))
        assertEquals(original, SlcanCodec.decode(encoded.dropLast(1).toByteArray()))
    }

    @Test
    fun `encodes the extended frame that was witnessed on can1`() {
        val encoded = SlcanCodec.encode(
            SlcanFrame(
                id = 0x12345678,
                data = byteArrayOf(0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77),
                extended = true,
            )
        )
        assertEquals("T1234567880011223344556677\r", String(encoded))
    }

    @Test
    fun `FD lengths above 8 use DLC codes`() {
        // 16 bytes is DLC code A, not literal 16.
        val encoded = SlcanCodec.encode(
            SlcanFrame(id = 0x100, data = ByteArray(16) { 0xEE.toByte() }, fd = true)
        )
        assertEquals('d', encoded[0].toInt().toChar())
        assertEquals("100", String(encoded, 1, 3))
        assertEquals('A', encoded[4].toInt().toChar())

        val decoded = SlcanCodec.decode(encoded.dropLast(1).toByteArray())!!
        assertEquals("DLC code A means 16 bytes", 16, decoded.data.size)
        assertTrue(decoded.fd)
    }

    @Test
    fun `b and B carry CAN FD frames with bitrate switching`() {
        // Treating these as unknown silently dropped every BRS frame, which is
        // most real FD traffic — the failure that FD testing on hardware exposed.
        val standard = SlcanCodec.decode("b1234DEADBEEF".toByteArray())!!
        assertEquals(0x123, standard.id)
        assertTrue(standard.fd)
        assertTrue(standard.brs)
        assertEquals(false, standard.extended)

        val extended = SlcanCodec.decode("B18DAF1104AABBCCDD".toByteArray())!!
        assertEquals(0x18DAF110, extended.id)
        assertTrue(extended.fd)
        assertTrue(extended.brs)
        assertTrue(extended.extended)
    }

    @Test
    fun `d stays FD without bitrate switching`() {
        val frame = SlcanCodec.decode("d4563AABBCC".toByteArray())!!
        assertTrue(frame.fd)
        assertEquals(false, frame.brs)
    }

    @Test
    fun `BRS survives an encode decode round trip`() {
        val original = SlcanFrame(
            id = 0x7AB, data = ByteArray(16) { 0xAB.toByte() }, fd = true, brs = true,
        )
        val encoded = SlcanCodec.encode(original)
        assertEquals('b', encoded[0].toInt().toChar())
        assertEquals(original, SlcanCodec.decode(encoded.dropLast(1).toByteArray()))
    }

    @Test
    fun `non-frame records are ignored rather than failing`() {
        assertNull("bare ACK", SlcanCodec.decode(byteArrayOf()))
        assertNull("BEL error byte", SlcanCodec.decode(byteArrayOf(0x07)))
        assertNull("free-text V reply", SlcanCodec.decode("WeAct Studio V1.0.0.0".toByteArray()))
    }

    @Test
    fun `truncated record is rejected instead of returning short data`() {
        // Claims 4 bytes, supplies 2.
        assertNull(SlcanCodec.decode("t1234DEAD".toByteArray().dropLast(2).toByteArray()))
    }

    @Test
    fun `assembler reunites frames split across USB packet boundaries`() {
        val assembler = SlcanFrameAssembler()
        // A 64-byte bulk packet routinely cuts the final frame in half.
        assertTrue(assembler.append("t1234DEADBEEF\rt7F".toByteArray()).size == 1)
        val rest = assembler.append("F101\rT18DAF1104AABBCCDD\r".toByteArray())
        assertEquals(2, rest.size)
        assertEquals(0x7FF, SlcanCodec.decode(rest[0])!!.id)
        assertEquals(0x18DAF110, SlcanCodec.decode(rest[1])!!.id)
    }

    @Test
    fun `assembler splits a saturated multi-frame packet`() {
        val assembler = SlcanFrameAssembler()
        val burst = "t3218" + "0011223344556677" + "\r" +
            "t3218" + "0111223344556677" + "\r" +
            "t3218" + "0211223344556677" + "\r"
        val records = assembler.append(burst.toByteArray())
        assertEquals(3, records.size)
        assertEquals(8, SlcanCodec.decode(records[0])!!.data.size)
    }

    @Test
    fun `configuration commands match what the device accepted`() {
        assertEquals("S6\r", String(SlcanCodec.bitrate(SlcanCodec.Bitrate.B500K)))
        assertEquals("Y5\r", String(SlcanCodec.dataBitrate(SlcanCodec.DataBitrate.D5M)))
        assertEquals("M0\r", String(SlcanCodec.mode(silent = false)))
        assertEquals("M1\r", String(SlcanCodec.mode(silent = true)))
        assertEquals("O\r", String(SlcanCodec.open()))
        assertEquals("C\r", String(SlcanCodec.close()))
        assertEquals(SlcanCodec.Bitrate.B500K, SlcanCodec.Bitrate.of(500_000))
    }
}
