"""构建期检查：NVRTC 能打开 cuda_fp16.h。不初始化 CUDA 设备。"""
from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_char_p, c_int, c_void_p
from pathlib import Path

from cupy._environment import _get_include_dir_from_conda_or_wheel


def _version(lib: str) -> tuple[int, int]:
    nvrtc = ctypes.CDLL(lib)
    major = c_int()
    minor = c_int()
    rc = nvrtc.nvrtcVersion(byref(major), byref(minor))
    if rc != 0:
        raise SystemExit(f"nvrtcVersion 失败: {lib} rc={rc}")
    return major.value, minor.value


def _include_dir(major: int, minor: int) -> Path:
    dirs = _get_include_dir_from_conda_or_wheel(major, minor)
    if not dirs:
        raise SystemExit(f"CuPy 没有为 NVRTC {major}.{minor} 找到头文件目录")
    header = Path(dirs[0]) / "cuda_fp16.h"
    if not header.is_file():
        raise SystemExit(f"缺少 {header}")
    return Path(dirs[0])


def _compile(lib: str, include: Path) -> None:
    nvrtc = ctypes.CDLL(lib)
    prog = c_void_p()
    source = b'#include <cuda_fp16.h>\nextern "C" __global__ void k() {}\n'
    nvrtc.nvrtcCreateProgram.argtypes = [
        POINTER(c_void_p),
        c_char_p,
        c_char_p,
        c_int,
        c_void_p,
        c_void_p,
    ]
    nvrtc.nvrtcCompileProgram.argtypes = [c_void_p, c_int, POINTER(c_char_p)]
    rc = nvrtc.nvrtcCreateProgram(byref(prog), source, b"k.cu", 0, None, None)
    if rc != 0:
        raise SystemExit(f"nvrtcCreateProgram 失败 rc={rc}")
    opts = [
        f"-I{include}".encode(),
        b"--device-as-default-execution-space",
        b"-arch=sm_75",
    ]
    arr = (c_char_p * len(opts))(*opts)
    rc = nvrtc.nvrtcCompileProgram(prog, len(opts), arr)
    if rc != 0:
        size = c_int()
        nvrtc.nvrtcGetProgramLogSize(prog, byref(size))
        buf = ctypes.create_string_buffer(max(size.value, 1))
        nvrtc.nvrtcGetProgramLog(prog, buf)
        raise SystemExit(buf.value.decode())


def main() -> None:
    # 运行时 CuPy 可能加载镜像自带的 libnvrtc，版本必须和头文件包一致。
    libs = ["libnvrtc.so.12"]
    for lib in libs:
        major, minor = _version(lib)
        include = _include_dir(major, minor)
        _compile(lib, include)
        print(f"CUDA_FP16_HEADER_OK lib={lib} nvrtc={major}.{minor} include={include}")


if __name__ == "__main__":
    main()
