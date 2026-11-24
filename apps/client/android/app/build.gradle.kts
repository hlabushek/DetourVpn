import java.io.File

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("dev.flutter.flutter-gradle-plugin")
}
val i028IsolatedPackage = providers.gradleProperty("i028IsolatedPackage").orNull == "true"
val clientApplicationId = "com.detourvpn.client"
val targetAbiFilters = providers.gradleProperty("target-platform").orNull
    ?.split(',')
    ?.map { it.trim() }
    ?.filter { it.isNotEmpty() }
    ?.map { platform ->
        when (platform) {
            "android-arm64" -> "arm64-v8a"
            "android-x64" -> "x86_64"
            else -> throw GradleException("Unsupported Flutter Android target platform: $platform")
        }
    }
    ?.distinct()
    ?.takeIf { it.isNotEmpty() }
    ?: listOf("arm64-v8a", "x86_64")
val betaSigningEnvironment = linkedMapOf(
    "DETOURVPN_BETA_KEYSTORE_PATH" to providers.environmentVariable("DETOURVPN_BETA_KEYSTORE_PATH").orNull,
    "DETOURVPN_BETA_KEYSTORE_PASSWORD" to providers.environmentVariable("DETOURVPN_BETA_KEYSTORE_PASSWORD").orNull,
    "DETOURVPN_BETA_KEY_ALIAS" to providers.environmentVariable("DETOURVPN_BETA_KEY_ALIAS").orNull,
    "DETOURVPN_BETA_KEY_PASSWORD" to providers.environmentVariable("DETOURVPN_BETA_KEY_PASSWORD").orNull,
)
val betaSigningProvided = betaSigningEnvironment.filterValues { !it.isNullOrBlank() }
if (betaSigningProvided.isNotEmpty() && betaSigningProvided.size != betaSigningEnvironment.size) {
    val missing = betaSigningEnvironment.keys - betaSigningProvided.keys
    throw GradleException("Incomplete beta signing environment; missing variables: ${missing.joinToString()}")
}
val betaSigningEnabled = betaSigningProvided.size == betaSigningEnvironment.size
if (betaSigningEnabled && clientApplicationId == "org.example.vpnclient") {
    throw GradleException("Choose a stable beta application ID before enabling release signing")
}
val betaKeystore = betaSigningEnvironment["DETOURVPN_BETA_KEYSTORE_PATH"]
    ?.takeIf { betaSigningEnabled }
    ?.let { File(it) }
if (betaKeystore != null) {
    if (!betaKeystore.isAbsolute) {
        throw GradleException("DETOURVPN_BETA_KEYSTORE_PATH must be an absolute path")
    }
    if (!betaKeystore.isFile) {
        throw GradleException("Configured beta keystore is unavailable")
    }
}
android {
    namespace = "org.example.vpnclient"
    compileSdk = 36
    ndkVersion = "28.2.13676358"
    buildToolsVersion = "36.0.0"
    defaultConfig {
        applicationId = clientApplicationId
        minSdk = 24
        targetSdk = 36
        versionCode = 23
        versionName = "0.1.0-beta.22"
        testInstrumentationRunner = if (providers.gradleProperty("reliabilityInstrumentation").orNull == "true")
            "org.example.vpnpoc.ReliabilityDeviceTests" else if (providers.gradleProperty("i028FlutterInstrumentation").orNull == "true")
            "androidx.test.runner.AndroidJUnitRunner" else "org.example.vpnpoc.I025DeviceTests"
        ndk { abiFilters += targetAbiFilters }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint { lintConfig = file("lint.xml") }
    // Compile the reviewed adapter and parser directly, without the I-011 UI.
    sourceSets["main"].java.apply {
        srcDir("../../../android/app/src/main/java")
        exclude("**/MainActivity.java", "**/PocApplication.java")
        // ADR-084: the beta uses the proven AWG GoBackend; the experimental Libbox engine is not built.
        exclude("**/LibboxProviderV1.java", "**/LibboxVpnService.java")
    }
    sourceSets["test"].java.srcDir("../../../android/app/src/test/java")
    packaging {
        jniLibs.useLegacyPackaging = false
        if (targetAbiFilters == listOf("arm64-v8a")) {
            jniLibs.excludes += "**/x86_64/**"
        } else if (targetAbiFilters == listOf("x86_64")) {
            jniLibs.excludes += "**/arm64-v8a/**"
        }
    }
    if (betaSigningEnabled) {
        signingConfigs {
            create("betaRelease") {
                storeFile = requireNotNull(betaKeystore)
                storePassword = requireNotNull(betaSigningEnvironment.getValue("DETOURVPN_BETA_KEYSTORE_PASSWORD"))
                keyAlias = requireNotNull(betaSigningEnvironment.getValue("DETOURVPN_BETA_KEY_ALIAS"))
                keyPassword = requireNotNull(betaSigningEnvironment.getValue("DETOURVPN_BETA_KEY_PASSWORD"))
            }
        }
    }
    buildTypes {
        debug {
            manifestPlaceholders["supportsAlwaysOn"] = "true"
            if (i028IsolatedPackage) applicationIdSuffix = ".i028"
        }
        release {
            manifestPlaceholders["supportsAlwaysOn"] = "false"
            isMinifyEnabled = false
            isShrinkResources = false
            if (betaSigningEnabled) {
                signingConfig = signingConfigs.getByName("betaRelease")
            }
        }
    }
}
flutter { source = "../.." }
kotlin { compilerOptions { jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17 } }
dependencies {
    implementation(project(":tunnel"))
    implementation("org.jetbrains.kotlin:kotlin-stdlib:2.2.20")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test:runner:1.3.0")
    androidTestImplementation("androidx.test:rules:1.2.0")
    androidTestImplementation("junit:junit:4.12")
}
configurations.configureEach { resolutionStrategy.activateDependencyLocking() }
dependencyLocking {
    // Flutter selects engine ABI artifacts per device. Their exact versions are
    // pinned by the SDK engine revision; locking their presence breaks device builds.
    ignoredDependencies.add("io.flutter:*")
}
