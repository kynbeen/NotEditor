package com.noteditor.app

import android.net.Uri
import android.os.Bundle
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    var pyApi: PyObject? = null
        private set

    private var genericFileCallback: ((String) -> Unit)? = null

    // PDF 복수 선택 런처 (문서 합치기)
    private val openPdfsLauncher = registerForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments()
    ) { uris: List<Uri>? ->
        val cb = genericFileCallback
        genericFileCallback = null
        if (uris.isNullOrEmpty()) {
            val emptyRes = JSONObject().apply {
                put("ok", true)
                put("cancelled", true)
                put("added", JSONArray())
                put("sources", JSONArray())
            }.toString()
            cb?.invoke(emptyRes)
            return@registerForActivityResult
        }

        Thread {
            try {
                val copiedPaths = mutableListOf<String>()
                for (uri in uris) {
                    val name = getFileName(uri) ?: "document_${System.currentTimeMillis()}.pdf"
                    val target = File(cacheDir, name)
                    copyUriToFile(uri, target)
                    copiedPaths.add(target.absolutePath)
                }
                val jsonPaths = JSONArray().put(JSONArray(copiedPaths)).toString()
                val resultJson = api.callAttr("dispatch_call", "add_paths", jsonPaths).toString()
                runOnUiThread { cb?.invoke(resultJson) }
            } catch (e: Exception) {
                val errJson = JSONObject().apply {
                    put("ok", false)
                    put("error", e.localizedMessage ?: "파일 처리 중 오류가 발생했습니다.")
                }.toString()
                runOnUiThread { cb?.invoke(errJson) }
            }
        }.start()
    }

    // 필기 원본 선택 런처 (.sdocx, .notewise, .goodnotes)
    private val openHandwritingSourceLauncher = registerForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri: Uri? ->
        val cb = genericFileCallback
        genericFileCallback = null
        if (uri == null) {
            val statusJson = pyApi?.callAttr("dispatch_call", "handwriting_status", "[]")?.toString() ?: "{}"
            cb?.invoke(statusJson)
            return@registerForActivityResult
        }

        Thread {
            try {
                val name = getFileName(uri) ?: "source_notes.sdocx"
                val target = File(cacheDir, name)
                copyUriToFile(uri, target)
                val argsJson = JSONArray().put(target.absolutePath).toString()
                val api = pyApi ?: throw IllegalStateException("파이썬 엔진이 아직 초기화되지 않았습니다.")
                val resultJson = api.callAttr("dispatch_call", "set_handwriting_source_path", argsJson).toString()
                runOnUiThread { cb?.invoke(resultJson) }
            } catch (e: Exception) {
                val errJson = JSONObject().apply {
                    put("ok", false)
                    put("error", e.localizedMessage ?: "필기 파일 로드 실패")
                }.toString()
                runOnUiThread { cb?.invoke(errJson) }
            }
        }.start()
    }

    // 필기 대상 PDF 선택 런처
    private val openHandwritingTargetLauncher = registerForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri: Uri? ->
        val cb = genericFileCallback
        genericFileCallback = null
        if (uri == null) {
            val statusJson = pyApi?.callAttr("dispatch_call", "handwriting_status", "[]")?.toString() ?: "{}"
            cb?.invoke(statusJson)
            return@registerForActivityResult
        }

        Thread {
            try {
                val name = getFileName(uri) ?: "target_document.pdf"
                val target = File(cacheDir, name)
                copyUriToFile(uri, target)
                val argsJson = JSONArray().put(target.absolutePath).toString()
                val api = pyApi ?: throw IllegalStateException("파이썬 엔진이 아직 초기화되지 않았습니다.")
                val resultJson = api.callAttr("dispatch_call", "set_handwriting_target_path", argsJson).toString()
                runOnUiThread { cb?.invoke(resultJson) }
            } catch (e: Exception) {
                val errJson = JSONObject().apply {
                    put("ok", false)
                    put("error", e.localizedMessage ?: "대상 PDF 로드 실패")
                }.toString()
                runOnUiThread { cb?.invoke(errJson) }
            }
        }.start()
    }

    // 문서 합치기 결과 저장 런처
    private var pendingOrderJson: String = "[]"
    private val saveResultLauncher = registerForActivityResult(
        ActivityResultContracts.CreateDocument("application/pdf")
    ) { uri: Uri? ->
        val cb = genericFileCallback
        genericFileCallback = null
        if (uri == null) {
            val cancelRes = JSONObject().apply {
                put("ok", true)
                put("cancelled", true)
            }.toString()
            cb?.invoke(cancelRes)
            return@registerForActivityResult
        }

        val order = pendingOrderJson
        Thread {
            try {
                val tempOutput = File(cacheDir, "merged_output_${System.currentTimeMillis()}.pdf")
                val args = JSONArray().put(JSONArray(order)).put(tempOutput.absolutePath).toString()
                val api = pyApi ?: throw IllegalStateException("파이썬 엔진이 아직 초기화되지 않았습니다.")
                val resultRaw = api.callAttr("dispatch_call", "build_result_to_path", args).toString()
                val resultObj = JSONObject(resultRaw)

                if (resultObj.optBoolean("ok", false)) {
                    copyFileToUri(tempOutput, uri)
                    tempOutput.delete()
                }
                runOnUiThread { cb?.invoke(resultRaw) }
            } catch (e: Exception) {
                val errJson = JSONObject().apply {
                    put("ok", false)
                    put("error", e.localizedMessage ?: "저장 실패")
                }.toString()
                runOnUiThread { cb?.invoke(errJson) }
            }
        }.start()
    }

    // 필기 옮기기 결과 저장 런처
    private var pendingHandwritingArgs: Triple<String, String, Boolean>? = null
    private val saveHandwritingLauncher = registerForActivityResult(
        ActivityResultContracts.CreateDocument("*/*")
    ) { uri: Uri? ->
        val cb = genericFileCallback
        val args = pendingHandwritingArgs
        genericFileCallback = null
        pendingHandwritingArgs = null

        if (uri == null || args == null) {
            val cancelRes = JSONObject().apply {
                put("ok", true)
                put("cancelled", true)
            }.toString()
            cb?.invoke(cancelRes)
            return@registerForActivityResult
        }

        Thread {
            try {
                val suggestedName = args.first
                val pagePlanJson = args.second
                val allowUnconfirmed = args.third

                val ext = if (suggestedName.contains(".")) suggestedName.substringAfterLast(".") else "sdocx"
                val tempOutput = File(cacheDir, "transfer_output_${System.currentTimeMillis()}.$ext")

                val pyArgs = JSONArray().apply {
                    put(tempOutput.absolutePath)
                    put(JSONArray(pagePlanJson))
                    put(allowUnconfirmed)
                }.toString()

                val api = pyApi ?: throw IllegalStateException("파이썬 엔진이 아직 초기화되지 않았습니다.")
                val resultRaw = api.callAttr("dispatch_call", "transfer_handwriting_to_path", pyArgs).toString()
                val resultObj = JSONObject(resultRaw)

                if (resultObj.optBoolean("ok", false)) {
                    copyFileToUri(tempOutput, uri)
                    tempOutput.delete()
                }
                runOnUiThread { cb?.invoke(resultRaw) }
            } catch (e: Exception) {
                val errJson = JSONObject().apply {
                    put("ok", false)
                    put("error", e.localizedMessage ?: "필기 이전 저장 실패")
                }.toString()
                runOnUiThread { cb?.invoke(errJson) }
            }
        }.start()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        setContentView(webView)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = true
            allowContentAccess = true
            useWideViewPort = true
            loadWithOverviewMode = true
            cacheMode = WebSettings.LOAD_DEFAULT
        }

        webView.webViewClient = object : WebViewClient() {}

        // JavaScript 브리지 등록
        webView.addJavascriptInterface(NotEditorBridge(this, webView), "AndroidBridge")

        // Chaquopy 파이썬 런타임 초기화
        try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(this))
            }
            val py = Python.getInstance()
            val appModule = py.getModule("noteditor.app")
            pyApi = appModule.callAttr("ComposerApi")
        } catch (t: Throwable) {
            t.printStackTrace()
            AlertDialog.Builder(this)
                .setTitle("엔진 초기화 오류")
                .setMessage("NotEditor 파이썬 엔진을 시작하는 중 오류가 발생했습니다:\n\n" + t.stackTraceToString())
                .setPositiveButton("확인", null)
                .show()
        }

        // 웹 UI 로드
        webView.loadUrl("file:///android_asset/index.html")
    }

    fun choosePdfs(callback: (String) -> Unit) {
        this.genericFileCallback = callback
        openPdfsLauncher.launch(arrayOf("application/pdf"))
    }

    fun chooseHandwritingSource(callback: (String) -> Unit) {
        this.genericFileCallback = callback
        openHandwritingSourceLauncher.launch(arrayOf("*/*"))
    }

    fun chooseHandwritingTarget(callback: (String) -> Unit) {
        this.genericFileCallback = callback
        openHandwritingTargetLauncher.launch(arrayOf("application/pdf"))
    }

    fun saveResult(orderJson: String, suggestedName: String, callback: (String) -> Unit) {
        this.genericFileCallback = callback
        this.pendingOrderJson = orderJson
        saveResultLauncher.launch(suggestedName)
    }

    fun saveHandwriting(suggestedName: String, pagePlanJson: String, allowUnconfirmed: Boolean, callback: (String) -> Unit) {
        this.genericFileCallback = callback
        this.pendingHandwritingArgs = Triple(suggestedName, pagePlanJson, allowUnconfirmed)
        saveHandwritingLauncher.launch(suggestedName)
    }

    private fun copyUriToFile(uri: Uri, destFile: File) {
        contentResolver.openInputStream(uri)?.use { input ->
            FileOutputStream(destFile).use { output ->
                input.copyTo(output)
            }
        }
    }

    private fun copyFileToUri(sourceFile: File, destUri: Uri) {
        contentResolver.openOutputStream(destUri)?.use { output ->
            sourceFile.inputStream().use { input ->
                input.copyTo(output)
            }
        }
    }

    private fun getFileName(uri: Uri): String? {
        var result: String? = null
        if (uri.scheme == "content") {
            val cursor = contentResolver.query(uri, null, null, null, null)
            cursor?.use {
                if (it.moveToFirst()) {
                    val index = it.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                    if (index >= 0) result = it.getString(index)
                }
            }
        }
        if (result == null) {
            result = uri.path
            val cut = result?.lastIndexOf('/') ?: -1
            if (cut != -1) result = result?.substring(cut + 1)
        }
        return result
    }
}
