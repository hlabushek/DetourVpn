plugins { id("com.android.library") }
val preparedPath = providers.gradleProperty("awgPreparedDir").orNull
    ?.takeIf { it.isNotBlank() }
    ?: "../../../.cache/i-011/sdk-go-only"
val prepared = project.file(preparedPath)
check(prepared.resolve("PREPARED.json").isFile) { "Run scripts/prepare_android_poc.py first" }
val preparedReceipt = groovy.json.JsonSlurper().parse(prepared.resolve("PREPARED.json")) as Map<*, *>
// Keep this fingerprint in sync with apps/android/patches/go-only.patch.
// Build snapshots intentionally omit patch files, so the reviewed hash is pinned here.
val expectedPatchHash = "523e726eb306af84addfa7c2ef6f4e0b6f89c8e4e9db7d015147752f843c2000"
check(preparedReceipt["patch_sha256"] == expectedPatchHash) {
    "Prepared GoBackend SDK patch hash is stale; prepare a fresh SDK tree matching apps/android/patches/go-only.patch and pass -PawgPreparedDir=<path>"
}
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
