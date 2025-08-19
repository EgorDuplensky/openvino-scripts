#!/usr/bin/env python3
"""build.py

Copyright (C) 2025 Intel Corporation
SPDX-License-Identifier: Apache-2.0

This script reproduces the key functionality of the original bash helper that
configures and builds the OpenVINO repository. All frequently-used command-line
switches are preserved, but argument parsing, path handling and process
invocation are done in pure Python for portability and readability.

Example:
    ./build.py -g --arch arm --enable-python \
        --enable-onnx-frontend --enable-tf-frontend -- target1 target2
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
# External package, json is too verbose
import yaml

# Reusable lists for frontends and plugins to avoid duplication
# Flags that should accept ON/OFF instead of boolean
ON_OFF_FLAGS = [
    # Debug
    "openvino-debug", "debug-caps",
    # Code quality
    "clang-format", "clang-tidy", "clang-tidy-fix", "cpplint",
    # Sanitizers
    "sanitizer", "thread_sanitizer", "ub_sanitizer",
    # Extra features,
    "python", "wheel", "samples", "tests", "docs",
    # System provided dependencies
    "system-pugixml", "system-snappy", "system-opencl", "system-tbb",
    "system-protobuf", "system-flatbuffers", "tbbbind-2-5",
    # Binary size optimizations
    "sse42", "avx2", "avx512f",
    # Extra third party
    "mlas-for-cpu", "kleidiai-for-cpu",
    # Test instrumentation and profiling
    "profiling-itt", "coverage", "fuzzing",
    # Build toggles
    "lto", "faster-build", "integritycheck", "qspectre",
    # API
    "cpp-api", "python-api", "genai-api",
    # Notebooks
    "notebooks",
    # OVMS
    "ovms",
    # Extra
    "cpu-specific-target-per-test"
]

FRONTENDS = ["onnx", "paddle", "tf", "tf_lite", "pytorch", "ir", "jax"]
PLUGINS = [
    "intel_cpu", "intel_gpu", "intel_npu",
    "hetero", "multi", "auto", "template", "auto_batch", "proxy",
]

ROOT = Path.cwd()


def find_repo_root() -> Path:
    """Locate the repository root using git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        root = Path(result.stdout.strip())
        if not root.exists():
            print("Error: Failed to find OpenVINO repository root", file=sys.stderr)
            sys.exit(1)
        return root
    except subprocess.CalledProcessError:
        print("Error: Not in a git repository", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: git command not found", file=sys.stderr)
        sys.exit(1)


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
    # Build type
    p.add_argument("-b", "--build-type", metavar="TYPE", default="Release",
                   choices=["Release", "Debug", "RelWithDebInfo"], help="CMAKE_BUILD_TYPE")
    p.add_argument("-j", "--parallel", nargs="?", const=-1, type=int, default=None,
                   help='The maximum number of concurrent processes to use when building')

    # Feature toggles (on / off)
    for flag in ON_OFF_FLAGS:
        p.add_argument(
            f"--enable-{flag}",
            choices=["on", "off"],
            help=f"Enable {flag.replace('-', ' ')} (on / off)"
        )

    # Another way to enable plugins
    p.add_argument(
        "--plugins", nargs='+', choices=PLUGINS,
        metavar="PLUGINS",
        help=f"List of plugins to enable (choices: {', '.join(PLUGINS)})"
    )

    # Another way to frontends plugins
    p.add_argument(
        "--frontends", nargs='+', choices=FRONTENDS,
        metavar="FRONTENDS",
        help=f"List of front-ends to enable (choices: {', '.join(FRONTENDS)})"
    )

    # Conditional compilation
    p.add_argument("--enable-cc", choices=["collect", "apply"],
                   help="Selective build config, 'collect' or 'apply' statistics")
    p.add_argument("--cc-stat-file", metavar="CSV", help="Stats CSV for --enable-cc apply stage")

    # Threading
    p.add_argument("--threading", choices=["TBB", "OMP", "SEQ"], default="TBB",
                   help="Threading backend")
    p.add_argument("--extra-modules", metavar="PATH", dest="extra_modules",
                   help="Path to extra OpenVINO modules (sets OPENVINO_EXTRA_MODULES)")
    # External libs
    p.add_argument("--opencv-dir", metavar="PATH", dest="opencv_dir",
                   help="Path to OpenCV installation (sets OpenCV_DIR)")
    p.add_argument("--output-root", metavar="PATH", dest="output_root",
                   help="Path for OUTPUT_ROOT CMake variable (defaults to source directory)")

    # Extra options
    # Ccache
    p.add_argument("--use-ccache", dest="use_ccache", action="store_true", default=True, help="Enable ccache")
    # Architecture
    p.add_argument("-a", "--arch", choices=["x86", "arm", "arm32", "riscv"],
                   help="Target architecture for cross-compilation")
    p.add_argument("-u", "--gprof", action="store_true", help="Enable gprof instrumentation")
    p.add_argument("--linux-perf", action="store_true", help="Add flags useful for Linux perf")
    p.add_argument("--native-compilation", action="store_true", help="Enable -march=native")
    p.add_argument("--use-mold", action="store_true", help="Use mold linker")
    p.add_argument("--use-ninja", action="store_true", help="Use Ninja build system")
    p.add_argument("--use-clang", metavar="VER", help="Use specific clang version")
    # Verbosity
    p.add_argument("-v", dest="verbose", action="count", help="Increase verbosity (-v, -vv, -vvv)", default=1)
    p.add_argument("-q", "--quiet", action="store_true", help="Don't show any progress status")
    # Shell completion emission
    p.add_argument("--completion", choices=["bash", "zsh", "fish"],
                   help="Generate shell completion script for specified shell")
    # Configuration export/import
    p.add_argument("--export", dest="export_file", metavar="FILE",
                   help="Export current parameters to a YAML file and exit")
    p.add_argument("--import", dest="import_file", nargs='?', const=".build", metavar="FILE",
                   help="Import parameters from a YAML file (default '.build')")
    p.add_argument("--ignore-config", action="store_true",
                   help="Ignore the default .build configuration file")

    p.add_argument("target", nargs=argparse.REMAINDER,
                   help="Targets passed verbatim to 'cmake --build'")

    argcomplete.autocomplete(p)
    # Installation to generate completions at runtime is handled via --completion flag

    return p


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
    if args.arch is not None:
        suffix = f"_{args.arch}"
    if args.threading == "OMP":
        suffix += "_omp"
    if args.native_compilation:
        suffix += "_native_comp"
    if args.enable_sanitizer:
        suffix += f"_{args.enable_sanitizer}"
    if args.enable_openvino_debug:
        suffix += "_ov_debug"
    return f"build_{args.build_type}{suffix}"


def _collect_cmake_defs(args) -> dict[str, str]:
    defs: dict[str, str] = {
        "CMAKE_BUILD_TYPE": args.build_type,
        "CMAKE_EXPORT_COMPILE_COMMANDS": "ON",
        # Set OUTPUT_ROOT to command line argument or default to source directory
        "OUTPUT_ROOT": args.output_root or str(ROOT),
    }

    # Generic --enable_* flags
    for name, value in vars(args).items():
        if name.startswith("enable_"):
            if name in [f"enable_{fe}_frontend" for fe in FRONTENDS] + \
                       [f"enable_{pl}_plugin" for pl in PLUGINS]:
                continue

            key = name[len("enable_"):].upper()
            if value in ("on", "off"):
                defs[f"ENABLE_{key}"] = value.upper()
            elif value is not None:
                flag_name = name[len('enable_'):]
                raise ValueError(f"Invalid value '{value}' for --enable-{flag_name}. Expected 'on' or 'off'.")

    # Threading
    defs["THREADING"] = args.threading
    # Sanitizers
    if args.enable_sanitizer:
        san_map = {
            "asan": "ENABLE_SANITIZER",
            "tsan": "ENABLE_THREAD_SANITIZER",
            "usan": "ENABLE_UB_SANITIZER",
            "msan": "ENABLE_MEMORY_SANITIZER",
        }
        defs[san_map[args.enable_sanitizer]] = "ON"
        defs["BUILD_SHARED_LIBS"] = "OFF"
    # ccache
    if args.use_ccache:
        defs["CMAKE_CXX_COMPILER_LAUNCHER"] = "ccache"
    # Frontends
    fe_list = set()
    if args.frontends:
        fe_list.update(args.frontends)
    for fe in FRONTENDS:
        if getattr(args, f"enable_{fe}_frontend", False):
            fe_list.add(fe)
    for fe in FRONTENDS:
        defs[f"ENABLE_OV_{fe.upper()}_FRONTEND"] = "ON" if fe in fe_list else "OFF"
    # Plugins
    pl_list = set()
    if args.plugins:
        pl_list.update(args.plugins)
    for pl in PLUGINS:
        if getattr(args, f"enable_{pl}_plugin", False):
            pl_list.add(pl)
    for pl in PLUGINS:
        defs[f"ENABLE_{pl.upper()}"] = "ON" if pl in pl_list else "OFF"
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
    if args.arch:
        # Toolchain
        toolchains = {
            "x86": "cmake/toolchains/x86_64.linux.toolchain.cmake",
            "arm": "cmake/arm64.toolchain.cmake",
            "arm32": "cmake/arm.toolchain.cmake",
            "riscv": "cmake/toolchains/riscv64-100-xuantie-gnu.toolchain.cmake",
        }

        defs['CMAKE_TOOLCHAIN_FILE'] = toolchains[args.arch]
        if args.arch == 'riscv':
            defs['RISCV_TOOLCHAIN_ROOT'] = '/opt/riscv'
    return defs


def _cmake_options(args) -> List[str]:
    return [f"-D{k}={v}" for k, v in _collect_cmake_defs(args).items()]


def add_arg(cmd: list[str], flag: str, value=None):
    """
    - If value is truthy and not a list/tuple → append flag + single value
    - If value is a list/tuple         → append flag + all values
    - If value is None or True (boolean flag)  → append flag only
    """
    if value is None:
        # flag with no value
        cmd.append(flag)
    elif value is True:
        # boolean flag, no value
        cmd.append(flag)
    elif isinstance(value, (list, tuple)):
        # flag followed by multiple values
        cmd.append(flag)
        cmd.extend(str(v) for v in value)
    elif value is not None:
        # flag followed by a single value
        cmd.extend([flag, str(value)])


def _load_config_file(file_path: str, parser: argparse.ArgumentParser, is_error_fatal: bool = True) -> dict:
    """Load and validate configuration from a YAML file.

    Args:
        file_path: Path to the configuration file
        parser: Argument parser to validate against
        is_error_fatal: If True, exit on file errors; if False, print warning and continue

    Returns:
        Dictionary of valid configuration options
    """
    try:
        with open(file_path) as f:
            loaded = yaml.safe_load(f) or {}
    except Exception as e:
        if is_error_fatal:
            print(f"Error: Unable to load import file {file_path}: {e}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Warning: Unable to load {file_path} file: {e}", file=sys.stderr)
            return {}

    # Filter only valid options
    valid_dests = {action.dest for action in parser._actions}
    valid_config = {}
    for k, v in loaded.items():
        if k in valid_dests:
            valid_config[k] = v
        else:
            file_type = "import" if is_error_fatal else Path(file_path).name
            print(f"Warning: Unknown option '{k}' in {file_type} file; ignoring", file=sys.stderr)

    return valid_config


def import_if_provided(parser: argparse.ArgumentParser) -> tuple[argparse.Namespace, dict]:
    """Handle configuration import and return parsed arguments with defaults.

    Performs two-phase parsing:
    1. Extract --import and --ignore-config flags
    2. Load .build by default unless --ignore-config is specified
    3. Load --import file if specified (overrides .build defaults)
    4. Parse all arguments with loaded defaults applied

    Returns:
        Tuple of (parsed arguments namespace, defaults dict from import file)
    """
    import_parser = argparse.ArgumentParser(add_help=False)
    import_parser.add_argument("--import", dest="import_file", nargs='?', const=".build", metavar="FILE")
    import_parser.add_argument("--ignore-config", action="store_true")
    known_args, remaining_argv = import_parser.parse_known_args()

    defaults: dict = {}

    # Load .build file by default unless --ignore-config is specified
    if not known_args.ignore_config and Path(".build").exists():
        defaults.update(_load_config_file(".build", parser, is_error_fatal=False))

    # Load --import file if specified (overrides .build defaults)
    if known_args.import_file:
        defaults.update(_load_config_file(known_args.import_file, parser,
                                          is_error_fatal=True))

    parser.set_defaults(**defaults)
    args = parser.parse_args(remaining_argv)

    # argparse REMAINDER resets to [] even with defaults, so restore imported target
    if 'target' in defaults and not args.target:
        args.target = defaults['target']

    # Restore import_file and ignore_config flags
    args.import_file = known_args.import_file
    args.ignore_config = known_args.ignore_config

    return args, defaults


def export_args(parser: argparse.ArgumentParser, args: argparse.Namespace, defaults: dict) -> None:
    """Export configuration parameters to YAML file and exit.

    Args:
        parser: The argument parser to determine which options were provided
        args: Parsed arguments namespace
        defaults: Dictionary of defaults loaded from import file
    """
    # Determine which parameters were explicitly provided via import or CLI
    provided = set(defaults.keys())
    # CLI-provided flags
    for action in parser._actions:
        for opt in action.option_strings:
            if opt in sys.argv:
                provided.add(action.dest)

    to_export: dict = {}
    for name, value in vars(args).items():
        if name in ("export_file", "import_file"):
            continue
        if name not in provided:
            continue
        to_export[name] = value
    try:
        with open(args.export_file, 'w') as f:
            yaml.safe_dump(to_export, f)
        print(f"Exported parameters to {args.export_file}")
    except Exception as e:
        print(f"Error: Unable to export to file {args.export_file}: {e}", file=sys.stderr)
        sys.exit(1)


def run() -> None:
    parser = _build_parser()
    args, defaults = import_if_provided(parser)

    # Handle export
    if args.export_file:
        export_args(parser, args, defaults)
        return

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

    # @todo: Consider fallback behavior when ninja is not available
    generator = ['-G', 'Ninja'] if args.use_ninja and shutil.which('ninja') else []
    # Prepare build dir
    build_dir = Path(_compute_build_dir(args))
    build_dir.mkdir(parents=True, exist_ok=True)
    _initial_env(args)

    # quiet overrules verbosity
    if args.quiet:
        args.verbose = 0

    cmake_cmd = [
        cmake_path,
        *generator,
        f"--log-level={'DEBUG' if args.verbose else 'ERROR'}",
        *_cmake_options(args),
        str(ROOT),
        '-B', str(build_dir)
    ]

    if args.verbose > 2:
        print('CMake command:', ' '.join(cmake_cmd))
        print('Build dir:', build_dir)

    # Configure step
    if args.configure and args.configure > 0:
        subprocess.run(cmake_cmd, check=True)
        # Exit after configure if -cc or --configure-only
        if (args.configure and args.configure > 1):
            return

    # Build step
    build_cmd = [cmake_path, '--build', str(build_dir)]

    if args.parallel is not None:
        if args.parallel != -1:
            add_arg(build_cmd, '--parallel', args.parallel)
        else:
            add_arg(build_cmd, '--parallel')

    add_arg(build_cmd, '--target', args.target)

    if args.verbose == 0:
        add_arg(build_cmd, '--', '--quiet')

    if args.verbose > 2:
        print('Build command:', ' '.join(build_cmd))

    subprocess.run(build_cmd, check=True)


if __name__ == '__main__':
    try:
        run()
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit code {e.returncode}")
        sys.exit(e.returncode)
