// Minimal JSON value + serializer (output only). No dependency is added for I-032 stage 1.
// Strings are escaped for quotes, backslashes and control characters; input is expected to be
// validated UTF-8 by the callers (DomainRule, profile parsing), and is never logged.
#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace detour {

class Json {
 public:
  using Array = std::vector<Json>;
  using Object = std::vector<std::pair<std::string, Json>>;  // insertion order is kept

  Json() : value_(Object{}) {}
  Json(bool v) : value_(v) {}                      // NOLINT(runtime/explicit)
  Json(int v) : value_(static_cast<int64_t>(v)) {}  // NOLINT(runtime/explicit)
  Json(int64_t v) : value_(v) {}                    // NOLINT(runtime/explicit)
  Json(const char* v) : value_(std::string(v)) {}   // NOLINT(runtime/explicit)
  Json(std::string v) : value_(std::move(v)) {}     // NOLINT(runtime/explicit)

  static Json MakeArray() {
    Json j;
    j.value_ = Array{};
    return j;
  }
  static Json MakeObject() { return Json(); }

  Json& Put(const std::string& key, Json value) {
    std::get<Object>(value_).emplace_back(key, std::move(value));
    return *this;
  }
  Json& Add(Json value) {
    std::get<Array>(value_).push_back(std::move(value));
    return *this;
  }

  std::string Dump() const {
    std::string out;
    Write(out);
    return out;
  }

 private:
  static void WriteString(std::string& out, const std::string& s) {
    static const char* hex = "0123456789abcdef";
    out.push_back('"');
    for (unsigned char c : s) {
      switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
          if (c < 0x20 || c == 0x7f) {
            out += "\\u00";
            out.push_back(hex[c >> 4]);
            out.push_back(hex[c & 15]);
          } else {
            out.push_back(static_cast<char>(c));
          }
      }
    }
    out.push_back('"');
  }

  void Write(std::string& out) const {
    if (const auto* b = std::get_if<bool>(&value_)) {
      out += *b ? "true" : "false";
    } else if (const auto* i = std::get_if<int64_t>(&value_)) {
      out += std::to_string(*i);
    } else if (const auto* s = std::get_if<std::string>(&value_)) {
      WriteString(out, *s);
    } else if (const auto* a = std::get_if<Array>(&value_)) {
      out.push_back('[');
      for (size_t k = 0; k < a->size(); ++k) {
        if (k) out.push_back(',');
        (*a)[k].Write(out);
      }
      out.push_back(']');
    } else {
      const auto& o = std::get<Object>(value_);
      out.push_back('{');
      for (size_t k = 0; k < o.size(); ++k) {
        if (k) out.push_back(',');
        WriteString(out, o[k].first);
        out.push_back(':');
        o[k].second.Write(out);
      }
      out.push_back('}');
    }
  }

  std::variant<bool, int64_t, std::string, Array, Object> value_;
};

}  // namespace detour
