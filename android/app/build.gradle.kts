plugins {
    id("com.android.application")
}

android {
    namespace = "ai.oscuro.filebridge"
    compileSdk = 36

    defaultConfig {
        applicationId = "ai.oscuro.filebridge"
        minSdk = 21
        targetSdk = 36
        versionCode = 1
        versionName = "0.1"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
}
