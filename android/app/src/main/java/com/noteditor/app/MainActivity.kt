package com.noteditor.app

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var bridge: NotEditorBridge

    private var openFileCallback: ((List<String>) -> Unit)? = null
    private var saveFileCallback: ((String) -> Unit)? = null

    // 파일 열기 SAF 런처
    private val openDocumentLauncher = registerForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments()
    ) { uris: List<Uri>? ->
        if (uris.isNullOrEmpty()) {
            openFileCallback?.invoke(emptyList())
            return@registerForActivityResult
        }
        val copiedPaths = mutableListOf<String>()
        for (uri in uris) {
            val fileName = getFileName(uri) ?: "document.pdf"
            val tempFile = File(cacheDir, fileName)
            contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(tempFile).use { output ->
                    input.copyTo(output)
                }
            }
            copiedPaths.add(tempFile.absolutePath)
        }
        openFileCallback?.invoke(copiedPaths)
    }

    // 파일 저장 SAF 런처
    private val createDocumentLauncher = registerForActivityResult(
        ActivityResultContracts.CreateDocument("*/*")
    ) { uri: Uri? ->
        if (uri == null) {
            saveFileCallback?.invoke("")
            return@registerForActivityResult
        }
        // SAF Uri를 브리지에 전달하여 저장 처리
        saveFileCallback?.invoke(uri.toString())
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Chaquopy 파이썬 런타임 초기화
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }

        webView = WebView(this)
        setContentView(webView)

        bridge = NotEditorBridge(this, webView)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = true
            allowContentAccess = true
            useWideViewPort = true
            loadWithOverviewMode = true
        }

        webView.webViewClient = object : WebViewClient() {}

        // pywebview 호환 JavaScript 인터페이스 등록 (기존 app.js 100% 호환)
        webView.addJavascriptInterface(bridge, "AndroidBridge")

        // pywebview 브리지 초기화 스크립트 주입
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                injectPywebviewCompat()
            }
        }

        // 웹 UI 로드
        webView.loadUrl("file:///android_asset/index.html")
    }

    private fun injectPywebviewCompat() {
        val js = """
            (function() {
                if (window.pywebview) return;
                window.pywebview = {
                    api: {
                        ping: function() {
                            return Promise.resolve(JSON.parse(window.AndroidBridge.ping()));
                        },
                        choose_files: function(filters) {
                            return new Promise(function(resolve) {
                                window._onFilesChosen = resolve;
                                window.AndroidBridge.chooseFiles();
                            });
                        },
                        choose_save_path: function(defaultName, extension) {
                            return new Promise(function(resolve) {
                                window._onSavePathChosen = resolve;
                                window.AndroidBridge.chooseSavePath(defaultName, extension);
                            });
                        },
                        call_python: function(methodName, argsJson) {
                            return Promise.resolve(JSON.parse(window.AndroidBridge.callPython(methodName, JSON.stringify(argsJson))));
                        }
                    }
                };
                window.dispatchEvent(new CustomEvent('pywebviewready'));
            })();
        """.trimIndent()
        webView.evaluateJavascript(js, null)
    }

    fun launchFilePicker(callback: (List<String>) -> Unit) {
        this.openFileCallback = callback
        openDocumentLauncher.launch(arrayOf("*/*"))
    }

    fun launchSavePicker(suggestedName: String, callback: (String) -> Unit) {
        this.saveFileCallback = callback
        createDocumentLauncher.launch(suggestedName)
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
