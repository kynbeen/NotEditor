package com.noteditor.app

import android.webkit.JavascriptInterface
import android.webkit.WebView
import org.json.JSONObject

class NotEditorBridge(
    private val activity: MainActivity,
    private val webView: WebView
) {

    @JavascriptInterface
    fun ping(): String {
        return JSONObject().apply {
            put("ok", true)
            put("platform", "android")
        }.toString()
    }

    @JavascriptInterface
    fun callPython(methodName: String, argsJson: String): String {
        return try {
            val api = activity.pyApi ?: return JSONObject().apply {
                put("ok", false)
                put("error", "파이썬 엔진이 아직 초기화되지 않았습니다.")
            }.toString()
            val result = api.callAttr("dispatch_call", methodName, argsJson)
            result.toString()
        } catch (e: Exception) {
            JSONObject().apply {
                put("ok", false)
                put("error", e.localizedMessage ?: "Python 실행 오류")
            }.toString()
        }
    }

    @JavascriptInterface
    fun choosePdfs() {
        activity.runOnUiThread {
            activity.choosePdfs { resultJson ->
                dispatchCallback(resultJson)
            }
        }
    }

    @JavascriptInterface
    fun chooseHandwritingSource() {
        activity.runOnUiThread {
            activity.chooseHandwritingSource { resultJson ->
                dispatchCallback(resultJson)
            }
        }
    }

    @JavascriptInterface
    fun chooseHandwritingTarget() {
        activity.runOnUiThread {
            activity.chooseHandwritingTarget { resultJson ->
                dispatchCallback(resultJson)
            }
        }
    }

    @JavascriptInterface
    fun saveResult(orderJson: String, suggestedName: String) {
        activity.runOnUiThread {
            activity.saveResult(orderJson, suggestedName) { resultJson ->
                dispatchCallback(resultJson)
            }
        }
    }

    @JavascriptInterface
    fun saveHandwriting(suggestedName: String, pagePlanJson: String, allowUnconfirmed: Boolean) {
        activity.runOnUiThread {
            activity.saveHandwriting(suggestedName, pagePlanJson, allowUnconfirmed) { resultJson ->
                dispatchCallback(resultJson)
            }
        }
    }

    private fun dispatchCallback(resultJson: String) {
        activity.runOnUiThread {
            // 자바스크립트 따옴표 이스케이프 처리
            val escaped = JSONObject.quote(resultJson)
            val js = "if (window._androidFileCallback) { window._androidFileCallback($escaped); }"
            webView.evaluateJavascript(js, null)
        }
    }
}
