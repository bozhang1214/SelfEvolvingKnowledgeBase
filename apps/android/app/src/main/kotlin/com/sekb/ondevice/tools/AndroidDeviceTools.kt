package com.sekb.ondevice.tools

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.provider.ContactsContract
import androidx.core.content.ContextCompat
import com.sekb.shared.model.ToolResult
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import com.sekb.shared.tools.DeviceTool
import com.sekb.shared.tools.PermissionChecker
import com.sekb.shared.tools.*

/** Android 权限检查实现（唯一接触 `checkSelfPermission` 的地方）。 */
class AndroidPermissionChecker(private val context: Context) : PermissionChecker {
    override fun has(permission: String): Boolean =
        ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
}

/**
 * 三个设备工具（RFC §9 的 M2 交付项）。
 *
 * 权限要求刻意的"两易一难"：
 * - `device_time` / `device_network`：不需要危险权限（随时可用）；
 * - `device_contacts_search`：需要 `READ_CONTACTS`（危险权限）——**它存在的意义**
 *   就是给"越权拦截率"提供可测的拦截路径：没授权时工具必须被闸门拦下并留审计，
 *   而不是静默返回空结果（静默失败会让用户以为"设备里没有这个人"）。
 */
object AndroidDeviceTools {

    fun all(context: Context): List<DeviceTool> = listOf(
        DeviceTool(
            name = "device_time",
            description = "查询设备当前日期与时间（含时区）",
            requiredPermission = null,
            args = emptyMap(),
            run = {
                val now = Date()
                val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.CHINA)
                ToolResult(ok = true, output = fmt.format(now) + "（" + java.util.TimeZone.getDefault().id + "）")
            },
        ),
        DeviceTool(
            name = "device_network",
            description = "查询当前网络类型与是否计量网络",
            requiredPermission = Manifest.permission.ACCESS_NETWORK_STATE,
            args = emptyMap(),
            run = {
                val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
                val caps = cm.activeNetwork?.let { cm.getNetworkCapabilities(it) }
                val type = when {
                    caps == null -> "无网络"
                    caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "WiFi"
                    caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "蜂窝"
                    caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "以太网"
                    else -> "其他"
                }
                val metered = caps?.let { !it.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) } ?: true
                ToolResult(ok = true, output = "$type，${if (metered) "按流量计费" else "不计费"}")
            },
        ),
        DeviceTool(
            name = "device_contacts_search",
            description = "按姓名关键字在本机联系人中搜索（最多 5 条）",
            requiredPermission = Manifest.permission.READ_CONTACTS,
            args = mapOf("query" to "姓名关键字"),
            run = { args ->
                val query = args["query"].orEmpty()
                if (query.isBlank()) {
                    ToolResult(ok = false, reason = "query 不能为空")
                } else {
                    val uri = ContactsContract.CommonDataKinds.Phone.CONTENT_URI
                    val projection = arrayOf(
                        ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                        ContactsContract.CommonDataKinds.Phone.NUMBER,
                    )
                    val selection = "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} LIKE ?"
                    val matches = mutableListOf<String>()
                    context.contentResolver.query(
                        uri, projection, selection, arrayOf("%$query%"), null,
                    )?.use { cursor ->
                        val nameIdx = cursor.getColumnIndex(projection[0])
                        val numIdx = cursor.getColumnIndex(projection[1])
                        while (cursor.moveToNext() && matches.size < 5) {
                            val name = cursor.getString(nameIdx) ?: "?"
                            val number = cursor.getString(numIdx) ?: "?"
                            matches.add("$name（${maskPhone(number)}）")
                        }
                    }
                    ToolResult(
                        ok = true,
                        output = if (matches.isEmpty()) "没有匹配的联系人" else matches.joinToString("；"),
                    )
                }
            },
        ),
    )

    /** 电话号码打码后再进模型上下文与日志：端侧 Agent 不该把完整号码写进任何可外传的文本。 */
    fun maskPhone(number: String): String {
        val digits = number.filter { it.isDigit() }
        if (digits.length < 7) return "***"
        return digits.take(3) + "****" + digits.takeLast(4)
    }
}
