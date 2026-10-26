package org.example.vpnpoc;

import androidx.test.rule.ActivityTestRule;
import dev.flutter.plugins.integration_test.FlutterTestRunner;
import org.junit.Rule;
import org.junit.runner.RunWith;

/** Test APK only: Flutter integration_test executes on Android while USB is absent. */
@RunWith(FlutterTestRunner.class)
public final class I028FlutterInstrumentationTest {
    @Rule public final ActivityTestRule<ClientActivity> rule =
        new ActivityTestRule<>(ClientActivity.class, true, false);
}
