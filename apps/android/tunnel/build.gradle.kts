plugins { id("com.android.library") }
val prepared = project.file("../../../.cache/i-011/sdk-go-only")
check(prepared.resolve("PREPARED.json").isFile) { "Run scripts/prepare_android_poc.py first" }
android {
    namespace = "org.amnezia.awg.tunnel"
    compileSdk = 36
    buildToolsVersion = "36.0.0"
    defaultConfig { minSdk = 24 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    sourceSets["main"].apply {
        java.setSrcDirs(listOf(prepared.resolve("tunnel/src/main/java")))
        java.exclude("**/AwgQuickBackend.java", "**/RootShell.java", "**/ToolsInstaller.java")
        jniLibs.setSrcDirs(listOf(prepared.resolve("jniLibs")))
        assets.setSrcDirs(listOf(prepared.resolve("assets")))
        manifest.srcFile(prepared.resolve("tunnel/src/main/AndroidManifest.xml"))
    }
}
dependencies {
    implementation("androidx.annotation:annotation:1.7.1")
    implementation("androidx.collection:collection:1.4.0")
    compileOnly("com.google.code.findbugs:jsr305:3.0.2")
}
configurations.configureEach { resolutionStrategy.activateDependencyLocking() }
