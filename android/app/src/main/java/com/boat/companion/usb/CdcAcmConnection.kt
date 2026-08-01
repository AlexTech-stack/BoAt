package com.boat.companion.usb

import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import java.io.Closeable

/**
 * Minimal USB CDC-ACM transport.
 *
 * Deliberately hand-rolled rather than pulling in usb-serial-for-android: that
 * library is not published to Maven Central (only JitPack, plus an unofficial
 * republish), and this device is a textbook CDC-ACM endpoint whose descriptors
 * are known — one control interface for line coding, one data interface with a
 * bulk pair. Vendor-specific serial chips would justify the dependency; this
 * does not.
 *
 * Descriptors as enumerated on the target phone:
 *   interface 0  class 2 (comm/ACM)  ep 0x82 interrupt IN — notifications, unused
 *   interface 1  class 10 (CDC data) ep 0x01 bulk OUT, ep 0x81 bulk IN, 64-byte
 */
class CdcAcmConnection private constructor(
    private val connection: UsbDeviceConnection,
    private val claimed: List<UsbInterface>,
    private val readEndpoint: UsbEndpoint,
    private val writeEndpoint: UsbEndpoint,
) : Closeable {

    /** Returns bytes written, or a negative value on failure. */
    fun write(bytes: ByteArray, timeoutMs: Int = WRITE_TIMEOUT_MS): Int =
        connection.bulkTransfer(writeEndpoint, bytes, bytes.size, timeoutMs)

    /**
     * Reads whatever is available, returning the byte count or a negative value
     * on timeout. A timeout is normal on a quiet bus and is not an error.
     */
    fun read(buffer: ByteArray, timeoutMs: Int = READ_TIMEOUT_MS): Int =
        connection.bulkTransfer(readEndpoint, buffer, buffer.size, timeoutMs)

    override fun close() {
        claimed.forEach { runCatching { connection.releaseInterface(it) } }
        runCatching { connection.close() }
    }

    companion object {
        const val VENDOR_ID = 0x0483
        const val PRODUCT_ID = 0x5740

        /** Bulk endpoints are 64 bytes; read several packets per call. */
        const val READ_BUFFER_SIZE = 4096
        private const val READ_TIMEOUT_MS = 200
        private const val WRITE_TIMEOUT_MS = 1000

        private const val REQUEST_TYPE_CLASS_INTERFACE_OUT = 0x21
        private const val SET_LINE_CODING = 0x20
        private const val SET_CONTROL_LINE_STATE = 0x22
        private const val DTR_RTS = 0x03

        /**
         * The WeAct adapter, or any attached CDC device if the exact ids are not
         * present — a re-flashed or cloned board may report different ids while
         * still being the same class of device.
         */
        fun find(manager: UsbManager): UsbDevice? {
            val devices = manager.deviceList.values
            return devices.firstOrNull { it.vendorId == VENDOR_ID && it.productId == PRODUCT_ID }
                ?: devices.firstOrNull { device ->
                    (0 until device.interfaceCount).any {
                        device.getInterface(it).interfaceClass == UsbConstants.USB_CLASS_COMM
                    }
                }
        }

        fun open(manager: UsbManager, device: UsbDevice): Result<CdcAcmConnection> {
            val connection = manager.openDevice(device)
                ?: return Result.failure(IllegalStateException("openDevice failed (permission?)"))

            var dataInterface: UsbInterface? = null
            var controlInterface: UsbInterface? = null
            for (i in 0 until device.interfaceCount) {
                val candidate = device.getInterface(i)
                when (candidate.interfaceClass) {
                    UsbConstants.USB_CLASS_COMM -> if (controlInterface == null) controlInterface = candidate
                    UsbConstants.USB_CLASS_CDC_DATA -> if (dataInterface == null) dataInterface = candidate
                }
            }
            val data = dataInterface ?: run {
                connection.close()
                return Result.failure(IllegalStateException("no CDC data interface"))
            }

            var readEndpoint: UsbEndpoint? = null
            var writeEndpoint: UsbEndpoint? = null
            for (i in 0 until data.endpointCount) {
                val endpoint = data.getEndpoint(i)
                if (endpoint.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
                if (endpoint.direction == UsbConstants.USB_DIR_IN) readEndpoint = endpoint
                else writeEndpoint = endpoint
            }
            if (readEndpoint == null || writeEndpoint == null) {
                connection.close()
                return Result.failure(IllegalStateException("missing bulk endpoint pair"))
            }

            val claimed = mutableListOf<UsbInterface>()
            // The control interface may be held by a kernel/system driver; force it.
            controlInterface?.let {
                if (connection.claimInterface(it, true)) claimed.add(it)
            }
            if (!connection.claimInterface(data, true)) {
                claimed.forEach { connection.releaseInterface(it) }
                connection.close()
                return Result.failure(IllegalStateException("could not claim data interface"))
            }
            claimed.add(data)

            // Baud rate is meaningless over USB, but STM32 CDC firmware expects a
            // valid line coding and DTR asserted before it will move data.
            val lineCoding = byteArrayOf(
                0x00, 0xC2.toByte(), 0x01, 0x00,  // 115200 baud, little-endian
                0x00,                             // 1 stop bit
                0x00,                             // no parity
                0x08,                             // 8 data bits
            )
            connection.controlTransfer(
                REQUEST_TYPE_CLASS_INTERFACE_OUT, SET_LINE_CODING, 0, 0,
                lineCoding, lineCoding.size, WRITE_TIMEOUT_MS,
            )
            connection.controlTransfer(
                REQUEST_TYPE_CLASS_INTERFACE_OUT, SET_CONTROL_LINE_STATE, DTR_RTS, 0,
                null, 0, WRITE_TIMEOUT_MS,
            )

            return Result.success(
                CdcAcmConnection(connection, claimed, readEndpoint, writeEndpoint)
            )
        }
    }
}
