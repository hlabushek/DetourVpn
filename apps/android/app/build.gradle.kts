plugins { id("com.android.application") }
android {
    namespace = "org.example.vpnpoc"
    compileSdk = 36
    buildToolsVersion = "36.0.0"
    defaultConfig {
        applicationId = "org.example.vpnpoc"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "0.1-i011"
        testInstrumentationRunner = if (providers.gradleProperty("i028Probe").orNull == "true")
            "org.example.vpnpoc.I028Probe" else "org.example.vpnpoc.PocDeviceTests"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    packaging { jniLibs.useLegacyPackaging = false }
}
dependencies {
    implementation(project(":tunnel"))
    testImplementation("junit:junit:4.13.2")
}
configurations.configureEach { resolutionStrategy.activateDependencyLocking() }
