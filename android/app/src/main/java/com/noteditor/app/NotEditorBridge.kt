package com.noteditor.app

import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import org.json.JSONArray
import org.json.JSONObject

class NotEditorBridge(
    private val activity: MainActivity,
    private val webView: WebView
) {
    private val mainHandler = Handler(Looper.getMainLooper())
    private val pyInstance: Python by lazy { Python.getInstance() }
    private val pyApi: PyObject by lazy {
        val appModule = pyInstance.getModule("noteditor.app")
        appModule.callAttr("ComposerApi")
    }

    @JavascriptInterface
    fun ping(): String {
        return JSONObject().apply {
            put("ok", true)
            put("platform", "android")
        }.toString()
    }

    @JavascriptInterface
    fun chooseFiles() {
        activity.runOnUiThread {
            activity.launchFilePicker { paths ->
                val jsonArray = JSONArray(paths)
                val js = "if (window._onFilesChosen) { window._onFilesChosen(${jsonArray}); delete window._onFilesChosen; }"
                webView.evaluateJavascript(js, null)
            }
        }
    }

    @JavascriptInterface
    fun chooseSavePath(defaultName: String, extension: String) {
        activity.runOnUiThread {
            activity.launchSavePicker(defaultName) { saveUri ->
                val js = "if (window._onSavePathChosen) { window._onSavePathChosen('${saveUri}'); delete window._onSavePathChosen; }"
                webView.evaluateJavascript(js, null)
            }
        }
    }

    @JavascriptInterface
    fun callPython(methodName: String, argsJson: String): String {
        return try {
            val result = pyApi.callAttr("dispatch_call", methodName, argsJson)
            result.toString()
        } catch (e: Exception) {
            JSONObject().apply {
                put("ok", false)
                put("error", e.localizedMessage ?: "Python execution error")
            }.toString()
        }
    }
}

