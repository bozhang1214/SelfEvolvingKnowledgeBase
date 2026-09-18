package com.sekb.ondevice

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.lifecycle.viewmodel.compose.viewModel
import com.sekb.ondevice.ui.ChatScreen
import com.sekb.ondevice.ui.ChatViewModel

/** 唯一 Activity：装配 ViewModel 与界面，不放业务逻辑。 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 自检入口（模拟器 E2E 用；不影响正常启动路径）：
        //   adb shell am start -n com.sekb.ondevice/.MainActivity --ez selftest true
        if (intent?.getBooleanExtra("selftest", false) == true) {
            runSelfTest()
        }

        setContent {
            MaterialTheme {
                Surface {
                    val vm: ChatViewModel = viewModel()
                    ChatScreen(vm)
                }
            }
        }
    }

    private fun runSelfTest() {
        val container = (application as SekbApp).container
        val email = intent?.getStringExtra("email").orEmpty()
        val password = intent?.getStringExtra("password").orEmpty()
        val sekb = intent?.getStringExtra("sekb").orEmpty()
        val cloud = if (email.isNotBlank() && password.isNotBlank() && sekb.isNotBlank()) {
            SelfTest.CloudParams(email, password, sekb)
        } else {
            null
        }
        Thread {
            try {
                val items = SelfTest.run(container, { line -> log(line) }, cloud)
                log(SelfTest.summary(items))
            } catch (e: Exception) {
                log("[FAIL] selftest_crashed — ${e.message}")
            }
        }.start()
    }

    private fun log(line: String) = Log.i("SEKB_SELFTEST", line)
}
