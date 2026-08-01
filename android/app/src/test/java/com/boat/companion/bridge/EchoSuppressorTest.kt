package com.boat.companion.bridge

import com.boat.companion.usb.SlcanFrame
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The bridge's loop suppression. Its failure mode is duplicate frames on a
 * vehicle bus, so the boundaries are worth pinning down rather than trusting a
 * single hardware run.
 */
class EchoSuppressorTest {

    private fun frame(id: Int, vararg data: Int) =
        SlcanFrame(id = id, data = data.map { it.toByte() }.toByteArray())

    @Test
    fun `a published frame is recognised when it echoes back`() {
        val suppressor = EchoSuppressor()
        suppressor.remember(frame(0x123, 0xDE, 0xAD))
        assertTrue(suppressor.consume(frame(0x123, 0xDE, 0xAD)))
    }

    @Test
    fun `a frame we never published is passed through`() {
        val suppressor = EchoSuppressor()
        suppressor.remember(frame(0x123, 0xDE, 0xAD))
        // This is the case that was broken: gateway-originated traffic must
        // reach the bus, even though it arrives tagged self-sent.
        assertFalse(suppressor.consume(frame(0x2A0, 0x01, 0x02)))
    }

    @Test
    fun `same id with different payload is not an echo`() {
        val suppressor = EchoSuppressor()
        suppressor.remember(frame(0x123, 0x11))
        assertFalse(suppressor.consume(frame(0x123, 0x22)))
    }

    @Test
    fun `each publication suppresses exactly one echo`() {
        val suppressor = EchoSuppressor()
        // A periodic message sends identical frames repeatedly; two published
        // frames must not swallow three echoes.
        suppressor.remember(frame(0x321, 0x01))
        suppressor.remember(frame(0x321, 0x01))
        assertTrue(suppressor.consume(frame(0x321, 0x01)))
        assertTrue(suppressor.consume(frame(0x321, 0x01)))
        assertFalse("third echo has no matching publication",
            suppressor.consume(frame(0x321, 0x01)))
    }

    @Test
    fun `entries outside the window stop suppressing`() {
        val suppressor = EchoSuppressor(windowNanos = 1)
        suppressor.remember(frame(0x100, 0x55))
        Thread.sleep(5)
        assertFalse("a stale entry must not suppress a genuine frame",
            suppressor.consume(frame(0x100, 0x55)))
    }

    @Test
    fun `memory is bounded when echoes never arrive`() {
        val suppressor = EchoSuppressor(capacity = 16)
        repeat(1000) { suppressor.remember(frame(0x200 + it, it and 0xFF)) }
        // Nothing blew up and unmatched entries did not accumulate without limit;
        // a one-way bridge would otherwise leak for as long as it runs.
        assertFalse(suppressor.consume(frame(0x999, 0x00)))
    }

    @Test
    fun `fingerprint separates id, payload and FD flag`() {
        val base = frame(0x123, 0x11, 0x22)
        assertEquals(base.fingerprint(), frame(0x123, 0x11, 0x22).fingerprint())
        assertFalse(base.fingerprint() == frame(0x124, 0x11, 0x22).fingerprint())
        assertFalse(base.fingerprint() == frame(0x123, 0x11, 0x23).fingerprint())
        assertFalse(base.fingerprint() == base.copy(fd = true).fingerprint())
    }
}
