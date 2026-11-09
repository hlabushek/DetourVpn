# Soft Orbit — design tokens

Single source for the Android client (`apps/client/lib/ui/tokens.dart`) and the Telegram Mini App
(`apps/telegram-mini-app/src/style.css`, `:root` custom properties). Values are taken from the
"09 / Soft Orbit · design system" reference sheet, not from the previous seed-based theme.
Names are identical in both places: CSS `--surface-2` ⇔ Dart `OrbitTokens.surface2`.

Dark is the primary theme; light is a full variant with the same names.

## Colour

| Token | Dark | Light | Use |
|---|---|---|---|
| `bg` | `#191624` | `#FAF5FF` | screen background |
| `surface-1` | `#241E33` | `#FFFFFF` | cards, navigation bar |
| `surface-2` | `#2A2340` | `#F4EDFC` | nested rows, inputs |
| `surface-3` | `#2E2742` | `#EADFF6` | pressed, selected tab pill, progress track |
| `outline` | `#BC93ED` @ 18 % | `#7753A5` @ 18 % | 1 px card borders |
| `accent` | `#BC93ED` | `#7753A5` | lavender: selection, progress, links, "Vpn" in the logo |
| `on-accent` | `#1E1430` | `#FFFFFF` | text on accent |
| `cta` | `#FF7E88` | `#A53649` | coral primary button |
| `on-cta` | `#33101C` | `#FFFFFF` | text on the primary button (dark ink keeps ≥ 4.5:1 on coral) |
| `success` | `#4CD964` | `#1F7A35` | "Доступен", credits |
| `warning` | `#F4B04D` | `#8A5300` | "Занят", stale-data notices |
| `danger` | `#E25B6B` | `#BA1A1A` | errors, destructive outlines, debits |
| `text` | `#F5EFFF` | `#2B2238` | primary text |
| `text-muted` | `#A89DBF` | `#6B5F7E` | captions, secondary text |
| `idle` | `#7D7393` | `#9A90AA` | "Недоступен" dot |

Server states map only from the catalog `reason` (ADR-092): `available` → `success` "Доступен",
`capacity` → `warning` "Занят", anything else → `idle` "Недоступен". No latency or speed is shown.

## Gradients and glow

- `orbit-fill`: linear 135°, `#FF8793` @ 32 % → `#AA7BEF` @ 8 % (light: 18 % → 6 %).
- `orbit-stroke`: linear `#B88AFF` → `#FF939C`, 1.4 px.
- `orbit-planet`: radial `#E6CEFF` → `#704D9C`.
- `glow-accent`: `accent` @ 24 %, blur 24 — selected server card, connected orbit.
- `glow-danger`: `danger` @ 22 %, blur 24 — error orbit.
- Background: `bg` with two soft radial glows (`accent` @ 10 % top right, `cta` @ 6 % bottom left).

## Shape and spacing

- Spacing scale (multiples of 8): `space-1` 8, `space-2` 16, `space-3` 24, `space-4` 32, `space-5` 40. Screen gutter 16 (24 on the
  welcome/empty states). 4 is allowed only inside compact rows (dot ↔ label).
- Radius: `radius-s` 12 (chips, badges, icon tiles), `radius-m` 16 (inputs, rows), `radius-l` 24
  (cards), `radius-pill` 999 (buttons, navigation pill).
- Elevation: cards have no Material elevation; `shadow-card` = `0 8 24 rgba(0,0,0,.28)` in dark,
  `0 8 24 rgba(119,83,165,.10)` in light.

## Typography (Inter, OFL-1.1)

| Token | Size / weight | Use |
|---|---|---|
| `display` | 40 / 700 | big number (balance) |
| `title-status` | 28 / 700 | connection status |
| `title-section` | 22 / 600 | screen titles |
| `title-card` | 16 / 600 | card and row titles |
| `body` | 15 / 400 | body text |
| `caption` | 13 / 500 | captions, state labels |
| `label` | 16 / 600 | buttons |

Inter is approved but **not yet bundled**: the official release could not be downloaded from this
machine, and copies found inside third-party apps were not used (unknown provenance). Until the
owner adds the official OFL files (Flutter `assets/fonts/` + `pubspec.yaml` `fonts:`; Mini App
`src/fonts/` + `@font-face`), Flutter renders Roboto and the Mini App the system sans-serif; sizes
and weights above already apply. The font must be bundled locally, never loaded from a CDN, so no
font request leaks the user's IP to a third party.

## Components and states

- **Primary button** (`cta`): height 56, pill. Disabled: `surface-3` with `text-muted`. Busy: same
  pill, spinner + "Подключение…".
- **Outlined danger button**: 1.5 px `danger`, transparent fill ("Отключить", "Отменить").
- **Secondary button**: `surface-2` fill, `text`.
- **Text action**: `accent`, no fill ("Сменить сервер", "Показать ещё").
- **Card**: `surface-1`, `radius-l`, 1 px `outline`. Selected: border 1.5 px `accent` + `glow-accent`
  + check badge. Disabled server: 55 % opacity.
- **Badge**: `radius-s`; "Гость" = `surface-3`/`text`; "Доступен" = `success` @ 14 % with dot.
- **Navigation**: `surface-1` bar, selected item in a `surface-3` pill, label `accent`.
- **Orbit states**: disconnected (power icon, muted stroke), connecting (animated arc), connected
  (check, `glow-accent`), error (`!`, `glow-danger`). The orbit is decorative and never represents a
  measurement.

## Not a source of truth

Demo values on the reference sheets ("Алексей", "12,4 ГБ из 50 ГБ", server lists) appear only in
TEST ONLY previews and are never shipped.
