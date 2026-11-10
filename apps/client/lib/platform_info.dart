import 'dart:io' show Platform;

/// Tests only: force the Windows or Android code paths regardless of the host OS.
bool? debugIsWindowsOverride;

/// True in the Windows beta client (I-032, ADR-095). Android remains the default path.
bool get isWindowsClient => debugIsWindowsOverride ?? Platform.isWindows;

/// Device platform sent at registration; must match the backend's `ck_devices_platform`.
String get devicePlatform => isWindowsClient ? 'windows' : 'android';

/// "на телефоне" / "на компьютере" for user-facing texts that name the device.
String get deviceWord => isWindowsClient ? 'на компьютере' : 'на телефоне';
