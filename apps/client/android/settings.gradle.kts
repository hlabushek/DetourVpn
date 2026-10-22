pluginManagement {
    val properties = java.util.Properties()
    file("local.properties").inputStream().use { properties.load(it) }
    val flutterSdk = properties.getProperty("flutter.sdk") ?: error("flutter.sdk missing")
    includeBuild("$flutterSdk/packages/flutter_tools/gradle")
    repositories { google(); mavenCentral(); gradlePluginPortal() }
}
plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "8.13.0" apply false
    id("com.android.library") version "8.13.0" apply false
    id("org.jetbrains.kotlin.android") version "2.2.20" apply false
}
rootProject.name = "vpn-flutter-client"
include(":app", ":tunnel")
project(":tunnel").projectDir = file("../../android/tunnel")
