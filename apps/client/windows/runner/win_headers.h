// Common Win32 include for the adapter headers (lean headers, no min/max macros).
// Only system_hooks.cpp uses Winsock, and it includes winsock2.h itself before anything else.
#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
