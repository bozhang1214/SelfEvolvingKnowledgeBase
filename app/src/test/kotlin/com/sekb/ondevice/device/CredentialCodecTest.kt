package com.sekb.ondevice.device

import com.sekb.ondevice.model.DeviceCredentials
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 凭证编解码与轮换时机（纯逻辑；Keystore 那层只能在真机/模拟器上验）。 */
class CredentialCodecTest {

    @Test
    fun `round trip preserves token exactly`() {
        val c = DeviceCredentials("dev-1", "eyJhbGciOi.payload.sig", 1000L, 2000L)
        assertEquals(c, CredentialCodec.decode(CredentialCodec.encode(c)))
    }

    @Test
    fun `malformed blob decodes to null instead of throwing`() {
        // 解不开时必须当作"没有凭证"（重新 enroll），绝不能让 App 崩在启动路径上
        assertNull(CredentialCodec.decode("garbage"))
        assertNull(CredentialCodec.decode("a\u0001b\u0001c\u0001d"))
        assertNull(CredentialCodec.decode("\u0001tok\u00011000\u00012000"))
    }

    @Test
    fun `rotation kicks in at one third of remaining life`() {
        val c = DeviceCredentials("d", "t", issuedAtMillis = 0L, expiresAtMillis = 3000L)
        assertFalse(c.needsRotation(nowMillis = 100L))     // 剩 2900/3000
        assertFalse(c.needsRotation(nowMillis = 1999L))    // 剩 1001 > 1000
        assertTrue(c.needsRotation(nowMillis = 2001L))     // 剩 999 < 1000 → 该换了
        assertTrue(c.isExpired(nowMillis = 3000L))
    }

    @Test
    fun `ttl comes from server not from a guess`() {
        val c = DeviceCredentials.fromTtl("d", "t", nowMillis = 1000L, ttlHours = 7 * 24)
        assertEquals(1000L + 7L * 24 * 3600 * 1000, c.expiresAtMillis)
        // 服务端把 TTL 压到 7 天时，1/3 阈值必须跟着变（而不是用"默认 30 天"反推）：
        // 7 天的 1/3 是 2.3 天，所以第 4 天起就该轮换
        assertFalse(c.needsRotation(nowMillis = 1000L + 2L * 24 * 3600 * 1000))
        assertTrue(c.needsRotation(nowMillis = 1000L + 5L * 24 * 3600 * 1000))
    }

    @Test
    fun `in memory store behaves like a store`() {
        val s = InMemoryCredentialStore()
        assertNull(s.load())
        val c = DeviceCredentials.fromTtl("d", "t", 0L, 1)
        s.save(c)
        assertEquals(c, s.load())
        s.clear()
        assertNull(s.load())
    }
}
