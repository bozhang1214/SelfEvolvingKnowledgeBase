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
        // 仅本机、解锁后可读：凭证是设备身份，不需要同步到 iCloud 或换机迁移
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        lastStatus = SecItemAdd(query as CFDictionary, nil)
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
