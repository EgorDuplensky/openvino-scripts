#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""build.py – Python replacement for build.sh

This script reproduces the key functionality of the original bash helper that
configures and builds the OpenVINO repository. All frequently-used command-line
switches are preserved, but argument parsing, path handling and process
invocation are done in pure Python for portability and readability.

Example:
    ./build.py -g --arch arm --enable-python \
        --enable-onnx-frontend --enable-tf-frontend -- target1 target2

Author: OpenAI ChatGPT
"""

from __future__ import annotations
import shutil
import argcomplete
import argparse
import os
import subprocess
import sys
from multiprocessing import cpu_count
from pathlib import Path
from typing import List

# Reusable lists for frontends and plugins to avoid duplication
FRONTENDS = ["onnx", "paddle", "tf", "tf_lite", "pytorch", "ir", "jax"]
PLUGINS = [
    "intel_cpu", "intel_gpu", "intel_gna", "intel_myriad_common",
    "intel_myriad", "hetero", "multi", "auto", "template", "auto_batch",
    "intel_npu", "proxy"
]

# ROOT = Path(__file__).resolve().parent
ROOT = Path.cwd()

def _nprocs_minus_two() -> int:
    """Return at least 1 and at most (nproc–2)."""
    return max(1, cpu_count() - 2)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build.py",
        description="Configure and build OpenVINO",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Generate only or full build
    p.add_argument("-c", dest="configure", action="count",
                   help=(
                       "-c :  Run CMake configure step\n"
                       "-cc : Run CMake configure step and exit (do not build)\n"
                   ))
    p.add_argument("-x", "--configure-only", action="store_true", help="Stop after CMake configure step")
    # Build type
    p.add_argument("-b", "--build-type", metavar="TYPE", default="Release",
                   choices=["Release", "Debug", "RelWithDebInfo"], help="CMAKE_BUILD_TYPE")

    # Plugins
    p.add_argument(
        "--enable-plugins", nargs='+', choices=PLUGINS,
        metavar="PLUGINS",
        help=f"List of plugins to enable (choices: {', '.join(PLUGINS)})"
    )
    for plugin in PLUGINS:
        p.add_argument(f"--enable-{plugin.replace('_','-')}-plugin", dest=f"enable_{plugin}_plugin",
                       action="store_true", help=f"Enable {plugin} plugin")

    # Frontends
    p.add_argument(
        "--enable-frontends", nargs='+', choices=FRONTENDS,
        metavar="FRONTENDS",
        help=f"List of front-ends to enable (choices: {', '.join(FRONTENDS)})"
    )
    p.add_argument("--enable-all-frontends", action="store_true", help="Enable every available front-end")
    for fe in FRONTENDS:
        p.add_argument(f"--enable-{fe.replace('_','-')}-frontend", dest=f"enable_{fe}_frontend",
                       action="store_true", help=f"Enable {fe} frontend")

    # Conditional compilation
    p.add_argument("--enable-cc", choices=["collect", "apply"], help="Selective build config, 'collect' or 'apply' statistics")
    p.add_argument("--cc-stat-file", metavar="CSV", help="Stats CSV for --enable-cc apply stage")

    # Threading
    p.add_argument("--threading", choices=["TBB", "OMP", "SEQ"], default="TBB",
                   help="Threading backend")

    # Debug
    p.add_argument("--enable-openvino-debug", action="store_true", help="Enable OV debug mode")
    p.add_argument("--enable-debug-caps", action="store_true", help="Enable debug capability checks")
    # Code quality
    p.add_argument("--enable-clang-format", action="store_true", help="Enable clang-format")
    p.add_argument("--enable-clang-tidy", action="store_true", help="Enable clang-tidy")
    p.add_argument("--enable-cpplint", action="store_true", help="Enable cpplint style check")
    # Sanitizers and instrumentation
    p.add_argument("-s", "--enable-sanitizer", choices=["address", "thread", "ub"],
                   help="Enable C/C++ sanitizers")
    p.add_argument("-l", "--enable-lto", action="store_true", help="Enable LTO")

    # Samples & extra modules
    p.add_argument("--enable-samples", action="store_true", help="Build OpenVINO Runtime samples")
    p.add_argument("--enable-wheel", action="store_true", help="Build Python wheels")
    p.add_argument("--extra-modules", metavar="PATH", dest="extra_modules",
                   help="Path to extra OpenVINO modules (sets OPENVINO_EXTRA_MODULES)")
    # System‐provided dependencies
    p.add_argument("--enable-system-pugixml", action="store_true", help="Use system pugixml")
    p.add_argument("--enable-system-protobuf", action="store_true", help="Use system protobuf")
    p.add_argument("--enable-system-flatbuffers", action="store_true", help="Use system FlatBuffers")
    p.add_argument("--enable-system-tbb", action="store_true", help="Use system TBB")
    p.add_argument("--enable-system-opencl", action="store_true", help="Use system OpenCL")
    # Binary‐size optimizations
    p.add_argument("--enable-tbbbind-2-5", dest="enable_tbbbind_2_5", action="store_true",
                   help="Enable prebuilt static TBBBind 2.5 usage")
    p.add_argument("--enable-sse42", action="store_true", help="Enable SSE4.2 optimizations")
    p.add_argument("--enable-avx2", action="store_true", help="Enable AVX2 optimizations")
    p.add_argument("--enable-avx512f", action="store_true", help="Enable AVX512F optimizations")
    p.add_argument("--enable-profiling-itt", action="store_true", help="Enable Intel ITT profiling")
    p.add_argument("--enable-mlas-for-cpu", action="store_true", help="Enable MLAS for CPU plugin")
    p.add_argument("--enable-kleidiai-for-cpu", action="store_true", help="Enable KleidiAI for CPU plugin")
    # Test instrumentation
    p.add_argument("--enable-coverage", action="store_true", help="Enable code coverage instrumentation")
    p.add_argument("--enable-fuzzing", action="store_true", help="Enable fuzzing instrumentation")
    # Build toggles
    p.add_argument('-j', '--parallel', type=int, default=0, help='The maximum number of concurrent processes to use when building')
    p.add_argument("--enable-faster-build", action="store_true",
                   help="Enable precompiled headers and unity build")
    p.add_argument("--enable-integritycheck", action="store_true",
                   help="Enable DLL integrity check (MSVC only)")
    p.add_argument("--enable-qspectre", action="store_true",
                   help="Enable /Qspectre flag (MSVC only)")
    # External libs
    p.add_argument("--opencv-dir", metavar="PATH", dest="opencv_dir",
                   help="Path to OpenCV installation (sets OpenCV_DIR)")
    p.add_argument("--tbb-root", metavar="PATH", dest="tbb_root",
                   help="Path to custom TBB installation (sets TBBROOT env var)")

    ### Extra options
    # Ccache
    p.add_argument("--use-ccache", dest="use_ccache", action="store_true", default=True, help="Enable ccache")
    # Architecture
    p.add_argument("-a", "--arch", choices=["x86", "arm", "arm32", "riscv"],
                   help="Target architecture for cross-compilation")
    p.add_argument("-u", "--gprof", action="store_true", help="Enable gprof instrumentation")
    p.add_argument("--linux-perf", action="store_true", help="Add flags useful for Linux perf")
    p.add_argument("--native-compilation", action="store_true", help="Enable -march=native")
    p.add_argument("--use-mold", action="store_true", help="Use mold linker")
    p.add_argument("--use-clang", metavar="VER", help="Use specific clang version")
    # Feature toggles
    p.add_argument("-p", "--enable-python", action="store_true", help="Build OpenVINO Python API")
    p.add_argument("-t", "--enable-tests", action="store_true", help="Build unit/functional tests")
    p.add_argument("--enable-docs", action="store_true", help="Build documentation")
    # Verbosity
    p.add_argument("-v", dest="verbose", action="count", help="Increase verbosity (-v, -vv, -vvv)", default=1)
    p.add_argument("-q", "--quiet", action="store_true", help="Don't show any progress status")
    # Shell completion emission
    p.add_argument("--completion", choices=["bash", "zsh", "fish"],
                   help="Generate shell completion script for specified shell")

    p.add_argument("target", nargs=argparse.REMAINDER,
                   help="Targets passed verbatim to 'cmake --build'")

    argcomplete.autocomplete(p)
    # Installation to generate completions at runtime is handled via --completion flag

    return p

def add_arg(cmd: list[str], flag: str, value):
    """
    - If value is truthy and not a list/tuple → append flag + single value
    - If value is a list/tuple         → append flag + all values
    - If value is True (boolean flag)  → append flag only
    - Otherwise (value is False/None)  → do nothing
    """
    if not value:
        # No value to add, skip
        return
    
    if value is True:
        # boolean flag, no value
        cmd.append(flag)
    elif isinstance(value, (list, tuple)):
        # flag followed by multiple values
        cmd.append(flag)
        cmd.extend(str(v) for v in value)
    elif value is not None:
        # flag followed by a single value
        cmd.extend([flag, str(value)])

def _initial_env(args) -> None:
    if args.use_ccache:
        os.environ.setdefault("CCACHE_DIR", str(Path.home() / ".ccache"))
        os.environ.setdefault("CCACHE_MAXSIZE", "50G")
    if args.native_compilation:
        for var in ("CFLAGS", "CXXFLAGS", "LDFLAGS"):
            os.environ[var] = os.environ.get(var, "") + " -march=native"
    if args.linux_perf:
        for var in ("CFLAGS", "CXXFLAGS"):
            os.environ[var] = os.environ.get(var, "") + " -fno-omit-frame-pointer -g -ggdb"
        os.environ["LDFLAGS"] = os.environ.get("LDFLAGS", "") + " -g"
    if args.gprof:
        for var in ("CFLAGS", "CXXFLAGS"):
            os.environ[var] = os.environ.get(var, "") + " -fno-omit-frame-pointer -g -pg"
        os.environ["LDFLAGS"] = os.environ.get("LDFLAGS", "") + " -g -pg"
    if args.use_clang:
        os.environ["CC"] = f"/usr/bin/clang-{args.use_clang}"
        os.environ["CXX"] = f"/usr/bin/clang++-{args.use_clang}"

def _compute_build_dir(args) -> str:
    suffix = ""
    if args.arch != "x86":
        suffix = f"_{args.arch}"
    if args.threading == "OMP":
        suffix += "_omp"
    if args.native_compilation:
        suffix += "_native_comp"
    if args.enable_sanitizer:
        suffix += f"_{args.enable_sanitizer}"
    if args.enable_debug_caps:
        suffix += "_no_debug_caps"
    if args.enable_openvino_debug:
        suffix += "_ov_debug"
    return f"build_{args.build_type}{suffix}"

def _collect_cmake_defs(args) -> dict[str, str]:
    defs: dict[str, str] = {
        "CMAKE_BUILD_TYPE": args.build_type,
        "ENABLE_CPP_API": "ON",
        # Always enable GAPI preprocessing as in original script
        "ENABLE_GAPI_PREPROCESSING": "ON",
    }
    # Generic --enable_* flags
    for name, value in vars(args).items():
        if name.startswith("enable_") and isinstance(value, bool):
            if name in [f"enable_{fe}_frontend" for fe in FRONTENDS] + \
                       [f"enable_{pl}_plugin" for pl in PLUGINS]:
                continue
            key = name[len("enable_"):].upper()
            defs[f"ENABLE_{key}"] = "ON" if value else "OFF"
    # Threading
    defs["THREADING"] = args.threading
    # Sanitizers
    if args.enable_sanitizer:
        san_map = {
            "address": "ENABLE_SANITIZER",
            "thread": "ENABLE_THREAD_SANITIZER",
            "ub": "ENABLE_UB_SANITIZER"
        }
        defs[san_map[args.enable_sanitizer]] = "ON"
        defs["BUILD_SHARED_LIBS"] = "OFF"
    # ccache
    if args.use_ccache:
        defs["CMAKE_CXX_COMPILER_LAUNCHER"] = "ccache"
    # Frontends
    fe_list = set()
    if args.enable_all_frontends:
        fe_list.update(FRONTENDS)
    if args.enable_frontends:
        fe_list.update(args.enable_frontends)
    for fe in FRONTENDS:
        if getattr(args, f"enable_{fe}_frontend", False):
            fe_list.add(fe)
    for fe in FRONTENDS:
        defs[f"ENABLE_OV_{fe.upper()}_FRONTEND"] = "ON" if fe in fe_list else "OFF"
    # Plugins
    pl_list = set()
    if args.enable_plugins:
        pl_list.update(args.enable_plugins)
    for pl in PLUGINS:
        if getattr(args, f"enable_{pl}_plugin", False):
            pl_list.add(pl)
    for pl in PLUGINS:
        defs[f"ENABLE_{pl.upper()}_PLUGIN"] = "ON" if pl in pl_list else "OFF"
    # Conditional compilation
    if args.enable_cc == 'collect':
        defs['SELECTIVE_BUILD'] = 'COLLECT'
        defs['ENABLE_PROFILING_ITT'] = 'ON'
    elif args.enable_cc == 'apply':
        defs['SELECTIVE_BUILD'] = 'ON'
        defs['ENABLE_PROFILING_ITT'] = 'OFF'
        defs['SELECTIVE_BUILD_STAT'] = args.cc_stat_file
    # Mold linker
    if args.use_mold:
        mold = "-fuse-ld=mold"
        defs.update({
            'CMAKE_EXE_LINKER_FLAGS': mold,
            'CMAKE_SHARED_LINKER_FLAGS': mold,
            'CMAKE_MODULE_LINKER_FLAGS': mold,
        })
    # Toolchain
    toolchains = {
        "x86": "cmake/toolchains/x86_64.linux.toolchain.cmake",
        "arm": "cmake/arm64.toolchain.cmake",
        "arm32": "cmake/arm.toolchain.cmake",
        "riscv": "cmake/toolchains/riscv64-100-xuantie-gnu.toolchain.cmake",
    }
    if args.arch:
        defs['CMAKE_TOOLCHAIN_FILE'] = toolchains[args.arch]
        if args.arch == 'riscv':
            defs['RISCV_TOOLCHAIN_ROOT'] = '/opt/riscv'
    return defs


def _cmake_options(args) -> List[str]:
    return [f"-D{k}={v}" for k, v in _collect_cmake_defs(args).items()]


def run() -> None:
    args = _build_parser().parse_args()
    # Shell completion provisioning
    if args.completion:
        exe = Path(sys.argv[0]).stem
        executables = [exe, exe + ".py"]
        code = argcomplete.shellcode(
            executables,
            shell=args.completion,
            use_defaults=True
        )
        sys.stdout.write(code)
        sys.exit(0)
    # Validate selective compilation
    if args.enable_cc == 'apply' and not args.cc_stat_file:
        print("Error: --cc-stat-file is required when --enable-cc apply", file=sys.stderr)
        sys.exit(1)
    # Strip argparse sentinel
    if '--' in args.target and not args.target[0]:
        args.target = args.target[1:]
    # Locate CMake
    cmake_path = shutil.which('cmake')
    if not cmake_path:
        print('Error: cmake not found in PATH', file=sys.stderr)
        sys.exit(1)
    generator = ['-G', 'Ninja'] if shutil.which('ninja') else []
    # Prepare build dir
    build_dir = Path(_compute_build_dir(args))
    build_dir.mkdir(parents=True, exist_ok=True)
    _initial_env(args)

    if args.quiet:
       args.verbose = 0
    
    cmake_cmd = [
        cmake_path,
        *generator,
        f"--log-level={'DEBUG' if args.verbose else 'ERROR'}",
        * _cmake_options(args),
        str(ROOT),
        '-B', str(build_dir)
    ]
    

    if args.verbose > 2:
        print('CMake command:', ' '.join(cmake_cmd))
        print('Build dir:', build_dir)
        # print(f"Building with {num_jobs} jobs...")

    # Configure step
    if args.configure and args.configure > 0:
        subprocess.run(cmake_cmd, check=True)
        if args.configure > 1:
            return
    # Build step
    build_cmd = [cmake_path,'--build', str(build_dir)]

    # 2) Single-value argument:
    add_arg(build_cmd, '--parallel', args.parallel)

    # 3) Multi-value argument:
    add_arg(build_cmd, '--target', args.target)

    subprocess.run(build_cmd, check=True)

if __name__ == '__main__':
    try:
        run()
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit code {e.returncode}")
        sys.exit(e.returncode)
