#ifndef HYPRLAND_COMPAT_HPP
#define HYPRLAND_COMPAT_HPP
#if __cplusplus >= 201703L
#include <string>
#include <string_view>
template<typename C, typename T, typename A>
std::basic_string<C, T, A> operator+(const std::basic_string<C, T, A>& lhs, std::basic_string_view<C, T> rhs) {
    return lhs + std::basic_string<C, T>(rhs);
}
template<typename C, typename T, typename A>
std::basic_string<C, T, A> operator+(std::basic_string_view<C, T> lhs, const std::basic_string<C, T, A>& rhs) {
    return std::basic_string<C, T>(lhs) + rhs;
}
#endif
#if __cplusplus >= 202002L
#include <ranges>
namespace std::ranges {
template<input_range R, typename T>
constexpr bool starts_with(R&& r, const T& t) {
    auto it = ranges::begin(r);
    auto tb = ranges::begin(t);
    const auto te = ranges::end(t);
    for (; tb != te; ++tb, ++it) {
        if (it == ranges::end(r) || !(*it == *tb))
            return false;
    }
    return true;
}
}
#endif
#endif
