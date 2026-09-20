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
        // 验收数字入口：工具调用 JSON 合法率的对照实验（约束解码 ON/OFF）
        //   adb shell am start -n com.sekb.ondevice/.MainActivity --ez eval true
        if (intent?.getBooleanExtra("eval", false) == true) {
            runToolCallEval()
        }
        // 端侧检索评测（命中率 + 延迟 + 阈值标定）
        //   adb shell am start -n com.sekb.ondevice/.MainActivity --ez evalrag true
        if (intent?.getBooleanExtra("evalrag", false) == true) {
            runRetrievalEval()
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
        SelfTestContextHolder.appContext = applicationContext
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
            } catch (e: Throwable) {
                // 必须是 Throwable：类初始化失败（ExceptionInInitializerError）与 OOM 都是 Error
                log("[FAIL] selftest_crashed — ${e.javaClass.simpleName}: ${e.message}")
            }
        }.start()
    }

    private fun runToolCallEval() {
        val container = (application as SekbApp).container
        Thread {
            try {
                val cfg = container.config
                val router = com.sekb.ondevice.route.PlaneRouter(cfg)
                val edge = com.sekb.ondevice.edge.OpenAiCompatibleEdgeLlm(
                    container.transport, cfg.edgeBaseUrl, config = cfg,
                )
                // 两个档位都测：语法约束的收益通常**只在小模型上**才显现，
                // 只测默认档会得出"约束解码没用"的片面结论。
                for (tier in listOf("short", "default")) {
                    val model = router.modelFor(tier)
                    edge.warmup(model)      // 预热：不预热的话第一组数据全是冷启动
                    val runner = com.sekb.ondevice.eval.ToolCallEvalRunner(
                        edge = edge, router = router, tools = container.tools, model = model,
                        hard = intent?.getBooleanExtra("hard", false) == true,
                    )
                    Log.i("SEKB_EVAL", "---- 档位 $tier → $model（${if (intent?.getBooleanExtra("hard", false) == true) "难档" else "易档"}）----")
                    val report = runner.run { line -> Log.i("SEKB_EVAL", line) }
                    for (line in runner.format(report).lines()) Log.i("SEKB_EVAL", line)
                }
            } catch (e: Exception) {
                Log.i("SEKB_EVAL", "[FAIL] eval_crashed — ${e.message}")
            }
        }.start()
    }

    private fun runRetrievalEval() {
        val container = (application as SekbApp).container
        Thread {
            try {
                val runner = com.sekb.ondevice.eval.RetrievalEvalRunner(container.embeddingProvider)
                val (index, store) = runner.buildIndex()
                val reports = runner.calibrate(index, store)
                val desc = if (container.onnxModelAvailable) {
                    "ONNX ${container.embeddingProvider.space.id}"
                } else {
                    "确定性桩 ${container.embeddingProvider.space.id}（未找到 ONNX 模型）"
                }
                for (line in runner.format(reports, desc).lines()) Log.i("SEKB_RAG_EVAL", line)
            } catch (e: Exception) {
                Log.i("SEKB_RAG_EVAL", "[FAIL] retrieval_eval_crashed — ${e.message}")
            }
        }.start()
    }

    private fun log(line: String) = Log.i("SEKB_SELFTEST", line)
}
