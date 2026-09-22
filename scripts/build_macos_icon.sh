#!/usr/bin/env bash
set -euo pipefail

# 从 SVG 生成 Tk 窗口图标和标准 macOS .icns。
# 依赖: rsvg-convert 或 ImageMagick (magick/convert) + macOS iconutil。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE="${PROJECT_ROOT}/assets/icons/ohscrcpy.svg"
PNG_DIR="${PROJECT_ROOT}/assets/icons"
MACOS_DIR="${PROJECT_ROOT}/assets/macos"
ICNS="${MACOS_DIR}/OHScrcpy.icns"

if command -v rsvg-convert >/dev/null 2>&1; then
    RENDERER="rsvg-convert"
    RENDERER_KIND="rsvg"
elif command -v magick >/dev/null 2>&1; then
    RENDERER="$(command -v magick)"
    RENDERER_KIND="imagemagick"
elif command -v convert >/dev/null 2>&1; then
    RENDERER="$(command -v convert)"
    RENDERER_KIND="imagemagick"
else
    echo "缺少 SVG 渲染器：请安装 rsvg-convert 或 ImageMagick" >&2
    exit 1
fi

if [[ ! -f "${SOURCE}" ]]; then
    echo "图标源文件不存在: ${SOURCE}" >&2
    exit 1
fi

if ! command -v iconutil >/dev/null 2>&1; then
    echo "缺少 iconutil：此脚本需要在 macOS 上生成 .icns" >&2
    exit 1
fi

mkdir -p "${PNG_DIR}" "${MACOS_DIR}"

render_png() {
    local size="$1"
    local output="$2"
    if [[ "${RENDERER_KIND}" == "rsvg" ]]; then
        "${RENDERER}" -w "${size}" -h "${size}" "${SOURCE}" -o "${output}"
    else
        "${RENDERER}" -background none "${SOURCE}" \
            -resize "${size}x${size}" -strip "PNG32:${output}"
    fi
}

render_png 256 "${PNG_DIR}/ohscrcpy-256.png"
render_png 1024 "${PNG_DIR}/ohscrcpy-1024.png"

ICONSET_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ohscrcpy-XXXXXX")"
ICONSET="${ICONSET_TMP}.iconset"
mv "${ICONSET_TMP}" "${ICONSET}"
cleanup() {
    rm -rf "${ICONSET}"
}
trap cleanup EXIT

for size in 16 32 128 256 512; do
    render_png "${size}" "${ICONSET}/icon_${size}x${size}.png"
    render_png "$((size * 2))" "${ICONSET}/icon_${size}x${size}@2x.png"
done

rm -f "${ICNS}"
iconutil -c icns "${ICONSET}" -o "${ICNS}"

echo "已生成:"
echo "  ${PNG_DIR}/ohscrcpy-256.png"
echo "  ${PNG_DIR}/ohscrcpy-1024.png"
echo "  ${ICNS}"
