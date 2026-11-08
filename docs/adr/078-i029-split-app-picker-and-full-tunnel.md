# ADR-078 — Explicit full-tunnel mode and searchable local app picker

Status: accepted for I-029 on 2026-11-03.

## Context

The Android split-routing screen exposed only include-selected and
exclude-selected modes. It listed package IDs without app labels, search, or
icons. Edits were held in widget state until a Save button was tapped, so
leaving the screen silently discarded unsaved changes and made the mode appear
to reset.

## Decision

- Add an explicit `all` routing preference labelled “Весь трафик через VPN”.
  It means no per-app exceptions: route ordinary app traffic through the VPN,
  while keeping DetourVpn's own control-plane traffic outside the tunnel. The
  native builder represents this as excluding only the DetourVpn package.
- Keep the existing `exclude` and `include` modes and their stored package list.
  The list is retained while `all` is selected but ignored by the tunnel until
  the user selects one of the split modes again. Existing preferences are not
  silently migrated.
- Put a split on/off switch above the mode controls. Turning split off selects
  `all`; turning it back on restores the last split mode. The user may choose
  `include` before selecting an app; an empty include list remains rejected
  when a tunnel profile is built, so the saved preference cannot fail open.
- Persist each mode or app-list change immediately to the existing
  device-local preference store. If native validation rejects a write, restore
  the last persisted UI state and show the reason. Included-app mode continues
  to reject an empty selection.
- Build the app picker from Android launcher-visible applications, showing the
  localized app label, package ID, and a small local icon. Search matches both
  label and package ID. Do not request `QUERY_ALL_PACKAGES`, upload the app list,
  or add a package dependency.

## Consequences and limits

Choosing `all` deliberately sends ordinary app traffic through the VPN. The
screen explains this before the selection takes effect. The app list and icons
remain on the device. Android Lockdown can still block apps outside the VPN
when a split mode is selected, and per-app routing is not a security boundary
against a hostile app. Physical routing, restart, DNS/IPv6, and Lockdown checks
remain required; this ADR does not grant release acceptance.
