#include "domain_rule.h"

#include <windows.h>
#include <winnls.h>

#include <algorithm>
#include <cstddef>
#include <string>
#include <vector>

namespace detour {
namespace {

[[noreturn]] void Invalid() { throw std::invalid_argument("domain_invalid"); }

bool IsDigit(char c) { return c >= '0' && c <= '9'; }

// Java: [0-9]{1,3}(\.[0-9]{1,3}){3}
bool LooksLikeIpv4(const std::string& s) {
  int groups = 0;
  size_t i = 0;
  while (i < s.size()) {
    size_t start = i;
    while (i < s.size() && IsDigit(s[i])) ++i;
    size_t len = i - start;
    if (len < 1 || len > 3) return false;
    ++groups;
    if (i == s.size()) break;
    if (s[i] != '.') return false;
    ++i;
    if (i == s.size()) return false;
  }
  return groups == 4;
}

// Java: [a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?
bool IsValidLabel(const std::string& label) {
  if (label.empty() || label.size() > 63) return false;
  auto alnum = [](char c) { return (c >= 'a' && c <= 'z') || IsDigit(c); };
  if (!alnum(label.front()) || !alnum(label.back())) return false;
  for (char c : label)
    if (!alnum(c) && c != '-') return false;
  return true;
}

// Number of Unicode code points (Java String.length counts UTF-16 units; BMP is the same).
size_t CodePoints(const std::string& s) {
  size_t n = 0;
  for (unsigned char c : s)
    if ((c & 0xC0) != 0x80) ++n;
  return n;
}

// Java String.trim(): removes characters <= U+0020 from both ends.
std::string Trim(const std::string& s) {
  size_t a = 0, b = s.size();
  while (a < b && static_cast<unsigned char>(s[a]) <= 0x20) ++a;
  while (b > a && static_cast<unsigned char>(s[b - 1]) <= 0x20) --b;
  return s.substr(a, b - a);
}

bool IsAscii(const std::string& s) {
  return std::all_of(s.begin(), s.end(), [](unsigned char c) { return c < 0x80; });
}

std::string AsciiLower(std::string s) {
  for (char& c : s)
    if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
  return s;
}

// IDNA conversion with STD3 rules (Java: IDN.toASCII(host, USE_STD3_ASCII_RULES)).
// Pure-ASCII hosts skip the Windows call; LDH validity is enforced by IsValidLabel afterwards.
std::string ToAscii(const std::string& host) {
  if (IsAscii(host)) return host;
  int wide_len = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, host.data(),
                                     static_cast<int>(host.size()), nullptr, 0);
  if (wide_len <= 0) Invalid();
  std::wstring wide(static_cast<size_t>(wide_len), L'\0');
  if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, host.data(), static_cast<int>(host.size()),
                          wide.data(), wide_len) != wide_len)
    Invalid();
  std::vector<wchar_t> out(512);
  int n = IdnToAscii(IDN_USE_STD3_ASCII_RULES, wide.c_str(), wide_len, out.data(),
                     static_cast<int>(out.size()));
  if (n <= 0) Invalid();
  std::string ascii;
  for (int i = 0; i < n; ++i) {
    if (out[static_cast<size_t>(i)] >= 0x80) Invalid();
    ascii.push_back(static_cast<char>(out[static_cast<size_t>(i)]));
  }
  return ascii;
}

}  // namespace

DomainRule ParseDomainRule(const std::string& input) {
  if (CodePoints(input) > 256) Invalid();
  std::string value = Trim(input);
  if (value.empty() || value.find("://") != std::string::npos ||
      value.find_first_of("/?#@:\\") != std::string::npos)
    Invalid();
  bool suffix = value.compare(0, 2, "*.") == 0;
  if (value.find('*') != std::string::npos && !suffix) Invalid();
  std::string host = suffix ? value.substr(2) : value;
  if (!host.empty() && host.back() == '.') host.pop_back();
  if (host.empty() || host.back() == '.' || LooksLikeIpv4(host)) Invalid();
  std::string ascii = AsciiLower(ToAscii(host));
  if (ascii.size() > 253 || (ascii.find('.') == std::string::npos && !suffix)) Invalid();
  size_t start = 0;
  while (true) {
    size_t dot = ascii.find('.', start);
    std::string label = ascii.substr(start, dot == std::string::npos ? std::string::npos : dot - start);
    if (!IsValidLabel(label)) Invalid();
    if (dot == std::string::npos) break;
    start = dot + 1;
  }
  DomainRule rule;
  rule.ascii_host = std::move(ascii);
  rule.suffix = suffix;
  return rule;
}

}  // namespace detour
