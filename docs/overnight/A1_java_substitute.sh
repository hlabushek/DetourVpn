#!/usr/bin/env bash
# javac + JUnit substitute for the Android unit tests (HANDOVER "Known environment issues").
set -u
# Usage (Git Bash): LOCALAPPDATA_POSIX="$(cygpath -u "$LOCALAPPDATA")" bash docs/overnight/A1_java_substitute.sh
WT="$(cd "$(dirname "$0")/../.." && pwd)"
# A fresh output folder each run (nothing is deleted); pass OUT=<empty dir> to choose it.
OUT="${OUT:-$(mktemp -d "$(cygpath -u "$TEMP")/detour-jvm-XXXXXX")}"
echo "output: $OUT"
JDK="/c/Program Files/Android/Android Studio/jbr/bin"
G="$HOME/.gradle/caches"
TUN="$WT/.cache/i-011/sdk-go-only-c6-20261117/tunnel/src/main/java"
mkdir -p "$OUT/classes" "$OUT/stub/io/flutter/plugins"
cat > "$OUT/stub/io/flutter/plugins/GeneratedPluginRegistrant.java" <<'EOF'
package io.flutter.plugins;
public final class GeneratedPluginRegistrant {
  public static void registerWith(io.flutter.embedding.engine.FlutterEngine e) {}
}
EOF
JARS=(
  "$LOCALAPPDATA_POSIX/Android/Sdk/platforms/android-36/android.jar"
  "$(ls $G/modules-2/files-2.1/io.flutter/flutter_embedding_debug/*/*/flutter_embedding_debug-1.0.0-*.jar | head -1)"
  "$(ls $G/modules-2/files-2.1/androidx.annotation/annotation-jvm/1.9.1/*/annotation-jvm-1.9.1.jar)"
  "$(ls $G/modules-2/files-2.1/com.google.code.findbugs/jsr305/2.0.1/*/jsr305-2.0.1.jar)"
  "$(ls $G/modules-2/files-2.1/androidx.collection/collection/1.1.0/*/collection-1.1.0.jar)"
  "$(ls $G/modules-2/files-2.1/androidx.lifecycle/lifecycle-common/2.7.0/*/lifecycle-common-2.7.0.jar)"
  "$(ls $G/8.14.3/transforms/*/transformed/lifecycle-runtime-2.7.0/jars/classes.jar | head -1)"
  "$(ls $G/modules-2/files-2.1/junit/junit/4.13.2/*/junit-4.13.2.jar)"
  "$(ls $G/modules-2/files-2.1/org.hamcrest/hamcrest-core/1.3/*/hamcrest-core-1.3.jar)"
)
CP=""
for j in "${JARS[@]}"; do [ -f "$j" ] || { echo "MISSING JAR: $j"; exit 2; }; CP="$CP;$(cygpath -w "$j")"; done
CP="${CP#;}"
find "$WT/apps/client/android/app/src/main/java" -name '*.java' ! -name GeneratedPluginRegistrant.java > "$OUT/src.txt"
find "$WT/apps/android/app/src/main/java" -name '*.java' ! -name MainActivity.java ! -name PocApplication.java ! -name LibboxProviderV1.java ! -name LibboxVpnService.java >> "$OUT/src.txt"
find "$TUN" -name '*.java' >> "$OUT/src.txt"
echo "$OUT/stub/io/flutter/plugins/GeneratedPluginRegistrant.java" >> "$OUT/src.txt"
find "$WT/apps/client/android/app/src/test/java" "$WT/apps/android/app/src/test/java" -name '*.java' > "$OUT/test.txt" 2>/dev/null
while read -r f; do cygpath -w "$f"; done < "$OUT/src.txt" > "$OUT/src.w"
while read -r f; do cygpath -w "$f"; done < "$OUT/test.txt" > "$OUT/test.w"
"$JDK/javac.exe" --release 17 -encoding UTF-8 -nowarn -d "$(cygpath -w "$OUT/classes")" -cp "$CP" @"$(cygpath -w "$OUT/src.w")" @"$(cygpath -w "$OUT/test.w")" 2>&1 | grep -v '^Note:' | tail -40
echo "javac exit=${PIPESTATUS[0]}"
CLS=$(cygpath -w "$OUT/classes")
# test classes: names of every test file
MAIN=(); EXTRA=()
while read -r f; do
  n=$(basename "$f" .java); [[ "$n" == *Test ]] || continue
  pkg=$(grep -m1 '^package ' "$f" | sed 's/package \(.*\);/\1/' | tr -d '\r')
  if [[ "$n" == RestartInPlacePatchTest || "$n" == SystemIntegrationWiringTest || "$n" == ConnectionWiringTest ]]; then EXTRA+=("$pkg.$n"); else MAIN+=("$pkg.$n"); fi
done < "$OUT/test.txt"
echo "== main run (${#MAIN[@]} classes)"
"$JDK/java.exe" -cp "$CLS;$CP" org.junit.runner.JUnitCore "${MAIN[@]}" > "$OUT/main.log" 2>&1; echo "main exit=$?"
grep -E '^(OK|Tests run|FAILURES)' "$OUT/main.log"
echo "== from apps/client/android/app (${EXTRA[*]})"
( cd "$WT/apps/client/android/app" && "$JDK/java.exe" -cp "$CLS;$CP" org.junit.runner.JUnitCore "${EXTRA[@]}" > "$OUT/extra.log" 2>&1; echo "extra exit=$?" )
grep -E '^(OK|Tests run|FAILURES)' "$OUT/extra.log"
