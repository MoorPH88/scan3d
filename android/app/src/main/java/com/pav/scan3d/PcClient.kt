package com.pav.scan3d

import android.content.Context
import android.net.ConnectivityManager
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.net.URL
import java.net.URLEncoder
import java.nio.ByteBuffer

/** Talks to ScanStation on the PC over the local Wi-Fi. */
class PcClient(private val base: String, private val key: String) {

    class HttpError(val code: Int, message: String) : IOException(message)

    data class Found(val host: String, val port: Int, val name: String)

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    private fun open(path: String, method: String, readTimeoutMs: Int = 30_000): HttpURLConnection {
        val c = URL(base + path).openConnection() as HttpURLConnection
        c.requestMethod = method
        c.connectTimeout = 6_000
        c.readTimeout = readTimeoutMs
        c.useCaches = false
        c.setRequestProperty("X-Scan-Key", key)
        return c
    }

    private fun finish(c: HttpURLConnection): String {
        try {
            val code = c.responseCode
            val stream = if (code in 200..299) c.inputStream else c.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code !in 200..299) {
                val msg = try {
                    JSONObject(body).optString("error", "PC answered HTTP $code")
                } catch (e: Exception) {
                    "PC answered HTTP $code"
                }
                throw HttpError(code, msg)
            }
            return body
        } finally {
            c.disconnect()
        }
    }

    private fun json(method: String, path: String, body: JSONObject? = null): JSONObject {
        val c = open(path, method)
        if (body != null) {
            val bytes = body.toString().toByteArray()
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.setFixedLengthStreamingMode(bytes.size)
            c.outputStream.use { it.write(bytes) }
        }
        return JSONObject(finish(c))
    }

    fun info(): JSONObject = json("GET", "/api/info")

    fun createScan(name: String, clientId: String, meta: JSONObject): JSONObject =
        json("POST", "/api/scans", JSONObject().put("name", name).put("client_id", clientId).put("meta", meta))

    fun uploadPhoto(scanId: String, file: File) {
        val c = open("/api/scans/${enc(scanId)}/images/${enc(file.name)}", "PUT", 120_000)
        c.doOutput = true
        c.setRequestProperty("Content-Type", "image/jpeg")
        c.setFixedLengthStreamingMode(file.length())
        c.outputStream.use { out -> file.inputStream().use { it.copyTo(out, 64 * 1024) } }
        finish(c)
    }

    fun process(scanId: String): JSONObject = json("POST", "/api/scans/${enc(scanId)}/process", JSONObject())

    fun status(scanId: String): JSONObject = json("GET", "/api/scans/${enc(scanId)}")

    fun list(): JSONArray {
        val c = open("/api/scans", "GET")
        return JSONArray(finish(c))
    }

    /** Download one of: model.stl, preview.png, report.json, log.txt */
    fun download(scanId: String, which: String, dest: File) {
        val c = open("/api/scans/${enc(scanId)}/file/$which", "GET", 120_000)
        try {
            val code = c.responseCode
            if (code !in 200..299) {
                val body = c.errorStream?.bufferedReader()?.use { it.readText() } ?: ""
                val msg = try {
                    JSONObject(body).optString("error", "HTTP $code")
                } catch (e: Exception) {
                    "HTTP $code"
                }
                throw HttpError(code, msg)
            }
            val tmp = File(dest.path + ".part")
            c.inputStream.use { input -> tmp.outputStream().use { input.copyTo(it, 64 * 1024) } }
            if (dest.exists()) dest.delete()
            if (!tmp.renameTo(dest)) throw IOException("Could not save ${dest.name}")
        } finally {
            c.disconnect()
        }
    }

    companion object {
        const val DISCOVERY_PORT = 8766
        private val MESSAGE = "SCAN3D_DISCOVER".toByteArray()

        /** Ask the local network "is ScanStation there?" and collect replies. */
        fun discover(ctx: Context, timeoutMs: Long = 2_000): List<Found> {
            val found = LinkedHashMap<String, Found>()
            val targets = mutableListOf<InetAddress>(InetAddress.getByName("255.255.255.255"))
            subnetBroadcast(ctx)?.let { targets.add(it) }
            DatagramSocket().use { s ->
                s.broadcast = true
                s.soTimeout = 300
                val end = System.currentTimeMillis() + timeoutMs
                var lastSend = 0L
                while (System.currentTimeMillis() < end) {
                    val now = System.currentTimeMillis()
                    if (now - lastSend > 600) {
                        for (t in targets) {
                            try {
                                s.send(DatagramPacket(MESSAGE, MESSAGE.size, t, DISCOVERY_PORT))
                            } catch (e: IOException) {
                                // try the other address
                            }
                        }
                        lastSend = now
                    }
                    try {
                        val buf = ByteArray(1024)
                        val p = DatagramPacket(buf, buf.size)
                        s.receive(p)
                        val j = JSONObject(String(p.data, 0, p.length))
                        val host = p.address?.hostAddress ?: ""
                        if (j.optString("app") == "ScanStation" && host.isNotEmpty()) {
                            found[host] = Found(host, j.optInt("port", 8765), j.optString("name", host))
                        }
                    } catch (e: SocketTimeoutException) {
                        // keep listening
                    } catch (e: Exception) {
                        // ignore stray packets
                    }
                }
            }
            return found.values.toList()
        }

        private fun subnetBroadcast(ctx: Context): InetAddress? {
            return try {
                val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return null
                val lp = cm.getLinkProperties(cm.activeNetwork) ?: return null
                for (la in lp.linkAddresses) {
                    val a = la.address
                    if (a is Inet4Address && !a.isLoopbackAddress) {
                        val ip = ByteBuffer.wrap(a.address).int
                        val prefix = la.prefixLength
                        val mask = if (prefix == 0) 0 else (-1 shl (32 - prefix))
                        val bcast = (ip and mask) or mask.inv()
                        return InetAddress.getByAddress(ByteBuffer.allocate(4).putInt(bcast).array())
                    }
                }
                null
            } catch (e: Exception) {
                null
            }
        }
    }
}
