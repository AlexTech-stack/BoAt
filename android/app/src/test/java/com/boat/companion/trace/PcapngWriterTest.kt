package com.boat.companion.trace

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Byte-level checks against the PCAPNG spec and `sdk/python/boat/pcapng.py`.
 *
 * The test also writes a fixture to `build/test-output/can-trace.pcapng` so the
 * file can be round-tripped through BoAt's own PcapngReader — a Kotlin test can
 * prove the bytes are what this code intended, but only the real reader proves
 * they are what the rest of the platform expects.
 */
class PcapngWriterTest {

    @Test
    fun `classic CAN frame matches the SocketCAN can_frame layout`() {
        val packed = PcapngWriter.packCanFrame(
            canId = 0x123,
            data = byteArrayOf(0x11, 0x44),
            fd = false,
        )

        assertEquals("can_frame is 16 bytes", 16, packed.size)
        // can_id is big-endian: the low byte lands last, not first.
        assertEquals(0x00.toByte(), packed[0])
        assertEquals(0x00.toByte(), packed[1])
        assertEquals(0x01.toByte(), packed[2])
        assertEquals(0x23.toByte(), packed[3])
        assertEquals("payload length", 2.toByte(), packed[4])
        assertEquals(0x11.toByte(), packed[8])
        assertEquals(0x44.toByte(), packed[9])
        assertEquals("unused payload is zero-padded", 0.toByte(), packed[10])
    }

    @Test
    fun `extended id sets CAN_EFF_FLAG in the top bit`() {
        val stream = ByteArrayOutputStream()
        PcapngWriter(stream).use { writer ->
            val iface = writer.addInterface("can0")
            writer.writeCan(iface, 1_000_000_000L, canId = 0x18DAF110, data = ByteArray(0), extended = true)
        }
        val epb = lastBlockBody(stream.toByteArray(), expectedType = 0x00000006)
        val canId = ByteBuffer.wrap(epb, 20, 4).order(ByteOrder.BIG_ENDIAN).int
        assertTrue("EFF flag must be set", canId and CAN_EFF_FLAG != 0)
        assertEquals("identifier survives alongside the flag", 0x18DAF110, canId and 0x1FFFFFFF)
    }

    @Test
    fun `CAN FD frame is 72 bytes and carries its flags`() {
        val packed = PcapngWriter.packCanFrame(
            canId = 0x7FF,
            data = ByteArray(12) { 0xAB.toByte() },
            fd = true,
            fdFlags = CANFD_BRS or CANFD_FDF,
        )
        assertEquals("canfd_frame is 72 bytes", 72, packed.size)
        assertEquals(12.toByte(), packed[4])
        assertEquals((CANFD_BRS or CANFD_FDF).toByte(), packed[5])
    }

    @Test
    fun `blocks are little-endian and length-framed at both ends`() {
        val stream = ByteArrayOutputStream()
        PcapngWriter(stream).use { it.addInterface("can0") }
        val bytes = stream.toByteArray()

        val buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        assertEquals("first block is an SHB", 0x0A0D0D0A, buffer.int)
        val shbLength = buffer.int
        assertEquals("SHB byte-order magic", 0x1A2B3C4D, buffer.int)
        // Trailing length must repeat the leading one.
        val trailing = ByteBuffer.wrap(bytes, shbLength - 4, 4).order(ByteOrder.LITTLE_ENDIAN).int
        assertEquals("SHB length repeated at block end", shbLength, trailing)
        assertEquals("blocks are 4-byte aligned", 0, shbLength % 4)
    }

    @Test
    fun `writes a fixture for cross-validation against the python reader`() {
        val output = File("build/test-output/can-trace.pcapng")
        output.parentFile?.mkdirs()

        output.outputStream().use { stream ->
            PcapngWriter(
                stream,
                comment = "Timestamps: host-side capture on the recording device. " +
                    "The SLCAN adapter firmware provides no hardware timestamps, so " +
                    "inter-frame gaps carry USB and scheduler jitter (~ms).",
                application = "BoAt companion (Android)",
            ).use { writer ->
                val can0 = writer.addInterface("can0")
                val can1 = writer.addInterface("can1")

                // 1_700_000_000 s after the epoch, then +1ms and +2ms.
                val base = 1_700_000_000_000_000_000L
                writer.writeCan(can0, base, 0x123, byteArrayOf(0x11, 0x44))
                writer.writeCan(can0, base + 1_000_000L, 0x055, byteArrayOf(0x55, 0x55))
                writer.writeCan(
                    can1, base + 2_000_000L, 0x18DAF110,
                    byteArrayOf(0xDE.toByte(), 0xAD.toByte(), 0xBE.toByte(), 0xEF.toByte()),
                    extended = true,
                )
                writer.writeCan(
                    can1, base + 3_000_000L, 0x7FF, ByteArray(16) { it.toByte() },
                    fd = true, fdFlags = CANFD_BRS or CANFD_FDF,
                )
            }
        }

        assertTrue("fixture was written", output.length() > 0)
        println("pcapng fixture: ${output.absolutePath} (${output.length()} bytes)")
    }

    /** Returns the body of the final block, verifying its type. */
    private fun lastBlockBody(bytes: ByteArray, expectedType: Int): ByteArray {
        var offset = 0
        var bodyStart = 0
        var bodyEnd = 0
        var lastType = 0
        while (offset + 12 <= bytes.size) {
            val header = ByteBuffer.wrap(bytes, offset, 8).order(ByteOrder.LITTLE_ENDIAN)
            lastType = header.int
            val length = header.int
            bodyStart = offset + 8
            bodyEnd = offset + length - 4
            offset += length
        }
        assertEquals("unexpected final block type", expectedType, lastType)
        return bytes.copyOfRange(bodyStart, bodyEnd)
    }
}
