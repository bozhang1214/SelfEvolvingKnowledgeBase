import Foundation
import Security
import SharedCore

/// iOS 侧的凭证存储（M5 端口②）：`Keychain` 实现共享层的 `CredentialStore`。
///
/// 与 Android 的对应关系：Android 用 **Keystore 里的 AES-GCM 密钥加密**后写 SharedPreferences；
/// iOS 直接用 **Keychain**（`kSecClassGenericPassword`）——它本身就是系统加密存储，
/// 再套一层应用层加密只会增加出错面（密钥管理、备份迁移）。
///
/// 编解码**复用共享层** `CredentialCodec`（本轮刚从 Android 模块搬过去）：
/// 两端同一个格式，才不会出现"Android 写的凭证 iOS 读不出来"。
///
/// 自检专用：`service` 前缀默认带 `selftest` 时使用独立的 service 名，
/// 避免无人值守自检把**真实凭证**覆盖掉。
final class KeychainCredentialStore: CredentialStore {

    private let service: String
    private let account = "device"

    /// 最近一次 Keychain 调用的 OSStatus（自检/排查用：`-34018` = 缺少 entitlement，
    /// 手搓 `.app`（未签名）时的典型症状）
    private(set) var lastStatus: OSStatus = errSecSuccess

    /// **写**那一步的 OSStatus。
    ///
    /// 为什么必须单独记：macOS 上第一次跑自检时，`keychain_roundtrip` 报了
    /// `OSStatus=-25300`（errSecItemNotFound）——那是**读**的状态，只说明"没读到"，
    /// 完全没说清是"没写进去"还是"读的条件对不上"。两个状态分开记，一次就能定位。
    private(set) var lastSaveStatus: OSStatus = errSecSuccess

    init(service: String = "tech.bos-studio.sekb.credentials") {
        self.service = service
    }

    func load() -> DeviceCredentials? {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        lastStatus = status
        guard status == errSecSuccess, let data = item as? Data,
              let plain = String(data: data, encoding: .utf8) else {
            return nil
        }
        return CredentialCodec.shared.decode(plain: plain)
    }

    func save(credentials: DeviceCredentials) {
        let plain = CredentialCodec.shared.encode(c: credentials)
        guard let data = plain.data(using: .utf8) else { return }
        // upsert：先删再写，避免 errSecDuplicateItem（Keychain 没有 "replace"）
        SecItemDelete(baseQuery() as CFDictionary)
        var query = baseQuery()
        query[kSecValueData as String] = data
        // 仅本机、解锁后可读：凭证是设备身份，不需要同步到 iCloud 或换机迁移。
        //
        // ⚠️ macOS 上**不能设这一项**（两条路都实测过，都不通，见 apps/mac/README.md）：
        //   · 设 `kSecAttrAccessible` 但不声明数据保护钥匙串 → macOS 落传统钥匙串，该项无效；
        //   · 声明 `kSecUseDataProtectionKeychain` → 写入直接 `-34018 errSecMissingEntitlement`，
        //     因为数据保护钥匙串要求 `keychain-access-group` entitlement，而它需要真实 team ID，
        //     ad-hoc 签名给不了。
        // 所以 macOS 走传统登录钥匙串、不设 accessible；iOS 保持数据保护钥匙串语义。
        #if !os(macOS)
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        #endif
        lastSaveStatus = SecItemAdd(query as CFDictionary, nil)
    }

    func clear() {
        SecItemDelete(baseQuery() as CFDictionary)
    }

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}
