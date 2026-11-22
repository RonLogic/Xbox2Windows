// Test-only draw observer. The provider renders the
// 64x64 test triangle offscreen, compares the readback against the CPU pixel rule
// and publishes the raw RGBA bytes so the check can compare runners byte for byte.
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

bool shim_draw_gpu(unsigned mode, std::vector<unsigned char>& rgba);

namespace {
constexpr unsigned side = 64;

bool publish(const void* data, DWORD size)
{
    DWORD written = 0;
    return WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), data, size, &written, nullptr) && written == size;
}

unsigned mismatches(const std::vector<unsigned char>& rgba, bool red)
{
    unsigned count = 0;
    for (unsigned y = 0; y < side; ++y) {
        for (unsigned x = 0; x < side; ++x) {
            const auto* pixel = rgba.data() + (y * side + x) * 4;
            const bool inside = x >= 8 && y >= 8 && x + y <= 63;
            const unsigned char expected[4]{static_cast<unsigned char>(inside && red ? 255 : 0),
                                            static_cast<unsigned char>(inside && !red ? 255 : 0), 0, 255};
            if (std::memcmp(pixel, expected, 4) != 0) ++count;
        }
    }
    return count;
}
} // namespace

extern "C" __declspec(dllexport) DWORD draw_request()
{
    char mode[16]{};
    const DWORD len = GetEnvironmentVariableA("X2W_SHIM_DRAW_CASE", mode, sizeof(mode));
    if (!len) return 0;
    if (len < sizeof(mode)) {
        if (!std::strcmp(mode, "draw")) return 1;
        if (!std::strcmp(mode, "flip")) return 2;
        if (!std::strcmp(mode, "two")) return 3;
        if (!std::strcmp(mode, "stale")) return 4;
        if (!std::strcmp(mode, "fail-second")) return 5;
        if (!std::strcmp(mode, "texture")) return 7;
        if (!std::strcmp(mode, "texture-stale")) return 9;
    }
    ExitProcess(21);
}

extern "C" __declspec(dllexport) DWORD draw_frame(DWORD mode)
{
    if (mode < 1 || mode > 10) ExitProcess(22);
    if (mode >= 3) std::fprintf(stderr, "draw_frame mode=%lu\n", static_cast<unsigned long>(mode));
    std::vector<unsigned char> rgba;
    if (!shim_draw_gpu(mode, rgba) || rgba.size() != side * side * 4) return 0xFFFFFFFF;
    if (!publish(rgba.data(), static_cast<DWORD>(rgba.size()))) ExitProcess(23);
    if (mode >= 7) return 0;  // The cross-runner reference checks all texture pixels.
    return mismatches(rgba, mode == 4 || mode == 5);
}
