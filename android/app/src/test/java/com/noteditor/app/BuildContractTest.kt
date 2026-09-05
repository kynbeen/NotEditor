package com.noteditor.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class BuildContractTest {
    @Test
    fun versionIsDerivedForEveryBuild() {
        assertFalse(BuildConfig.VERSION_NAME.isBlank())
        assertTrue(BuildConfig.VERSION_CODE > 0)
    }

    @Test
    fun packagedUiContainsAndroidBridgeAdapter() {
        val appJs = File("build/generated/noteditor/assets/app.js")
        assertTrue("generated app.js is missing", appJs.isFile)
        assertTrue(appJs.readText().contains("window.AndroidBridge.callPython"))
        assertTrue(appJs.readText().contains("window.AndroidBridge.choosePdfs"))
    }
}
