import com.google.protobuf.gradle.proto

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.protobuf)
}

android {
    namespace = "com.boat.companion"
    compileSdk {
        version = release(36) {
            minorApiLevel = 1
        }
    }

    defaultConfig {
        applicationId = "com.boat.companion"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            optimization {
                enable = false
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
    }

    // The gateway's .proto files are the single source of truth — consumed in
    // place from the C++/Python tree, never copied into the app.
    sourceSets {
        getByName("main") {
            proto { srcDir(rootProject.file("../boat-platform/proto")) }
        }
    }
}

protobuf {
    protoc { artifact = "com.google.protobuf:protoc:${libs.versions.protobuf.get()}" }
    plugins {
        create("grpc") {
            artifact = "io.grpc:protoc-gen-grpc-java:${libs.versions.grpc.get()}"
        }
        create("grpckt") {
            artifact =
                "io.grpc:protoc-gen-grpc-kotlin:${libs.versions.grpcKotlin.get()}:jdk8@jar"
        }
    }
    generateProtoTasks {
        all().forEach { task ->
            // protobuf-lite: the full runtime is far too heavy for an APK.
            // maybeCreate: whether AGP pre-registers the java builtin varies by
            // plugin version, and create()/named() each blow up on the wrong one.
            task.builtins {
                maybeCreate("java").option("lite")
                maybeCreate("kotlin").option("lite")
            }
            task.plugins {
                maybeCreate("grpc").option("lite")
                maybeCreate("grpckt").option("lite")
            }
        }
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.grpc.okhttp)
    implementation(libs.grpc.protobuf.lite)
    implementation(libs.grpc.stub)
    implementation(libs.grpc.kotlin.stub)
    implementation(libs.protobuf.kotlin.lite)
    // Generated gRPC stubs reference javax.annotation.Generated, absent on Android.
    compileOnly(libs.javax.annotation.api)
    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}