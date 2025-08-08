#!/usr/bin/env bash

current_dir=$(dirname -- "$0")
set -e
usage() {
    echo "build.sh <options> -b <Release|Debug|RelWithDebInfo> -- <target>"
    exit 0
}

target=all
generate=false
linux_perf_mode=false
enable_gprof=false
use_ccache=true
arm=false
riscv=false
configure_only=false
verbose=0
enable_sanitizer=""
threading="TBB"
enable_lto=false
enable_python=false
enable_frontends="ir"
enable_debug_caps=true
clang_version=""
enable_tests=true
enable_docs=false
native_compilation=false
enable_openvino_debug=false
enable_clang_format=true
enable_clang_tidy=false
enable_mold=true
enable_cc=""
mlir=false

getopt_cmd=getopt

unameOut="$(uname -s)"
case "${unameOut}" in
    Linux*)     getopt_cmd=getopt;;
    Darwin*)    getopt_cmd=$(ls /opt/homebrew/Cellar/gnu-getopt/*/bin/getopt);;
    CYGWIN*)    getopt_cmd=getopt;;
    MINGW*)     getopt_cmd=getopt;;
    MSYS_NT*)   getopt_cmd=getopt;;
    *)          exit 1
esac

GETOPT=$($getopt_cmd \
--options \
gcb:n:s:v:ptrlaxr \
--longoptions \
help,\
arm,\
arm32,\
riscv,\
linux-perf,\
enable-lto,\
enable-docs,\
enable-python,\
enable-tests,\
enable-all-frontends,\
enable-sanitizer:,\
threading:,\
enable-frontends:,\
disable-debug-caps,\
use-clang:,\
native-compilation,\
enable-openvino-debug,\
enable-clang-format,\
enable-clang-tidy,\
enable-mold,\
mlir,\
enable-cc: \
--name 'build.sh ' -- "$@")

if ! eval set -- "$GETOPT"; then
    echo "Terminating..." >&2
    exit 1
fi
eval set -- "$GETOPT"

while true; do
    case "$1" in
        -g) generate=true; shift ;;
        -c) use_ccache=false; shift ;;
        -b) build_type="${2}"; shift 2 ;;
        -n) echo "${2}"; shift 2 ;;
        -s | --enable-sanitizer) enable_sanitizer="${2}"; shift 2 ;;
        -p | --enable-python) enable_python=true; shift ;;
        -t | --enable-tests) enable_tests=true; shift ;;
        -r | --riscv) riscv=true; shift ;;
        -u | --enable-gprof) enable_gprof=true; shift ;;
        -l | --enable-lto) enable_lto=true; shift ;;
        --enable-docs) enable_docs=true; shift ;;
        -a | --arm) arm=true; shift ;;
        --arm32) arm32=true; shift ;;
        -x) configure_only=true; shift ;;
        -v) verbose=${2}; shift 2 ;;
        --linux-perf) linux_perf_mode=true; shift ;;
        --enable-frontends) enable_frontends="${2}"; shift 2 ;;
        --enable-all-frontends) enable_frontends="onnx paddle tf tf_lite pytorch ir"; shift ;;
        --threading) threading="${2}"; shift 2 ;;
        --disable-debug-caps) enable_debug_caps=false; shift ;;
        --use-clang) clang_version="${2}"; shift 2 ;;
        --native-compilation) native_compilation=true; shift ;;
        --enable-openvino-debug) enable_openvino_debug=true; shift ;;
        --enable-clang-format) enable_clang_format=true; shift ;;
        --enable-clang-tidy) enable_clang_tidy=true; shift ;;
        --enable-mold) enable_mold=true; shift ;;
        --enable-cc) enable_cc="${2}"; shift 2 ;;
        --mlir) mlir=true; shift ;;
        --help) usage; break ;;
        -- ) shift; break ;;
        *) usage; break ;;
    esac
done
target="$*"

suffix=""

CMAKE_OPTIONS+=" -DENABLE_CPP_API=ON"

if [[ -n "$OS" ]]; then
    suffix="${suffix}_${OS}"
    CMAKE_OPTIONS+=" -DOS_FOLDER=centos8"
fi

# if [[ $(lsb_release --release | grep -Eo "[0-9][0-9]\.[0-9][0-9]" 2> /dev/null) == "20.04" ]]; then
#     CMAKE_OPTIONS+=" -DPYTHON_EXECUTABLE=$(command -v python3)"
#     CMAKE_OPTIONS+=" -DPYTHON_LIBRARY=/usr/lib/x86_64-linux-gnu/libpython3.8.so"
#     CMAKE_OPTIONS+=" -DPYTHON_INCLUDE_DIR=/usr/include/python3.8"
#     echo "Ubuntu 20.04"
# else
#     CMAKE_OPTIONS+=" -DPYTHON_EXECUTABLE=$(command -v python3)"
#     CMAKE_OPTIONS+=" -DPYTHON_LIBRARY=/usr/lib/x86_64-linux-gnu/libpython3.6m.so"
#     CMAKE_OPTIONS+=" -DPYTHON_INCLUDE_DIR=/usr/include/python3.6m"
# fi

if [ "$native_compilation" = true ]; then
    export CFLAGS="${CFLAGS} -march=native"
    export CXXFLAGS="${CXXFLAGS} -march=native"
    export LDFLAGS="${LDFLAGS} -march=native"
    suffix+="_native_comp"
fi

if [ "$linux_perf_mode" = true ]; then
    export CFLAGS="${CFLAGS} -fno-omit-frame-pointer -g -ggdb"
    export CXXFLAGS="${CXXFLAGS} -fno-omit-frame-pointer -g -ggdb"
    export LDFLAGS="${LDFLAGS} -g"
fi

if [ "$enable_gprof" = true ]; then
    export CFLAGS="${CFLAGS} -fno-omit-frame-pointer -g -pg"
    export CXXFLAGS="${CXXFLAGS} -fno-omit-frame-pointer -g -pg"
    export LDFLAGS="${LDFLAGS} -g -pg"
fi

if [[ "$build_type" = "RelWithDebInfo" ]]; then
    export CFLAGS="${CFLAGS} -fno-omit-frame-pointer"
    export CXXFLAGS="${CXXFLAGS} -fno-omit-frame-pointer"
fi

if [[ -n "$enable_sanitizer" ]]; then
    suffix+="_$enable_sanitizer"
    export CFLAGS="${CFLAGS} -fno-omit-frame-pointer -g -ggdb"
    export CXXFLAGS="${CXXFLAGS} -fno-omit-frame-pointer -g -ggdb"

    CMAKE_OPTIONS+=" -DBUILD_SHARED_LIBS=OFF"

    if [[ "$enable_sanitizer" == "asan" ]]; then
        CMAKE_OPTIONS+=" -DENABLE_SANITIZER=ON"
    elif [[ "$enable_sanitizer" == "tsan" ]]; then
        CMAKE_OPTIONS+=" -DENABLE_THREAD_SANITIZER=ON"
    elif [[ "$enable_sanitizer" == "usan" ]]; then
        CMAKE_OPTIONS+=" -DENABLE_UB_SANITIZER=ON"
    elif [[ "$enable_sanitizer" == "msan" ]]; then
        CMAKE_OPTIONS+=" -DENABLE_MEMORY_SANITIZER=ON"
    else
        echo "Incorrect enable_sanitizer option: $enable_sanitizer"
        exit 1
    fi
fi

if [[ -n "$threading" ]]; then
    CMAKE_OPTIONS+=" -DTHREADING=$threading"
    if [[ "$threading" == "OMP" ]]; then
        suffix+="_omp"
    fi
fi

if [ "$enable_lto" = true ]; then
    CMAKE_OPTIONS+=" -DENABLE_LTO=ON"
    # CMAKE_OPTIONS+=" -DBUILD_SHARED_LIBS=OFF"
fi

if [ "$enable_docs" = true ]; then
    CMAKE_OPTIONS+=" -DENABLE_DOCS=ON"
    # CMAKE_OPTIONS+=" -DBUILD_SHARED_LIBS=OFF"
fi

if [[ -n "$CC" ]]; then
    CMAKE_OPTIONS+=" -DCMAKE_C_COMPILER=$CC"
fi

if [[ -n "$CXX" ]]; then
    CMAKE_OPTIONS+=" -DCMAKE_CXX_COMPILER=$CXX"
fi

### Legacy options
# CMAKE_OPTIONS+=" -DNGRAPH_UNIT_TEST_ENABLE=ON"
# CMAKE_OPTIONS+=" -DENABLE_TEMPLATE_PLUGIN=OFF"
# CMAKE_OPTIONS+=" -DENABLE_CLDNN=OFF"
# CMAKE_OPTIONS+=" -DENABLE_VPU=OFF"
# CMAKE_OPTIONS+=" -DENABLE_MKLDNN=ON"
# CMAKE_OPTIONS+=" -DENABLE_GNA=OFF"
# CMAKE_OPTIONS+=" -DENABLE_AUTO_BATCH=OFF"

### Plugins
CMAKE_OPTIONS+=" -DENABLE_INTEL_CPU=ON"
CMAKE_OPTIONS+=" -DENABLE_INTEL_GPU=ON"
CMAKE_OPTIONS+=" -DENABLE_INTEL_NPU=ON"
CMAKE_OPTIONS+=" -DENABLE_INTEL_MYRIAD=OFF"
CMAKE_OPTIONS+=" -DENABLE_INTEL_GNA=OFF"
CMAKE_OPTIONS+=" -DENABLE_INTEL_MYRIAD_COMMON=OFF"
CMAKE_OPTIONS+=" -DENABLE_HETERO=ON"
CMAKE_OPTIONS+=" -DENABLE_MULTI=ON"
CMAKE_OPTIONS+=" -DENABLE_TEMPLATE=ON"
CMAKE_OPTIONS+=" -DENABLE_AUTO=ON"
# CMAKE_OPTIONS+=" -DENABLE_TEMPLATE=OFF"
#CMAKE_OPTIONS+=" -DENABLE_TEMPLATE_REGISTRATION=ON"
CMAKE_OPTIONS+=" -DENABLE_TEMPLATE_REGISTRATION=OFF"
################################################
### Tests

if [ "$enable_tests" = true ]; then
    CMAKE_OPTIONS+=" -DENABLE_TESTS=ON"
    CMAKE_OPTIONS+=" -DENABLE_FUNCTIONAL_TESTS=ON"
fi

# CMAKE_OPTIONS+="- DENABLE_OV_CORE_UNIT_TEST=ON"
# CMAKE_OPTIONS+=" -DENABLE_GAPI_TESTS=ON"
################################################

### Frontends
if grep -E -q "\bir\b" <<< "${enable_frontends}" >/dev/null 2>&1; then
    CMAKE_OPTIONS+=" -DENABLE_OV_IR_FRONTEND=ON"
else
    CMAKE_OPTIONS+=" -DENABLE_OV_IR_FRONTEND=OFF"
fi

if grep -E -q "\bonnx\b" <<< "${enable_frontends}" >/dev/null 2>&1; then
    CMAKE_OPTIONS+=" -DENABLE_OV_ONNX_FRONTEND=ON"
else
    CMAKE_OPTIONS+=" -DENABLE_OV_ONNX_FRONTEND=OFF"
fi

if grep -E -q "\bpaddle\b" <<< "${enable_frontends}" >/dev/null 2>&1; then
    CMAKE_OPTIONS+=" -DENABLE_OV_PADDLE_FRONTEND=ON"
else
    CMAKE_OPTIONS+=" -DENABLE_OV_PADDLE_FRONTEND=OFF"
fi

if grep -E -q "\btf\b" <<< "${enable_frontends}" >/dev/null 2>&1; then
    CMAKE_OPTIONS+=" -DENABLE_OV_TF_FRONTEND=ON"
else
    CMAKE_OPTIONS+=" -DENABLE_OV_TF_FRONTEND=OFF"
fi

if grep -E -q "\btf_lite\b" <<< "${enable_frontends}" >/dev/null 2>&1; then
    CMAKE_OPTIONS+=" -DENABLE_OV_TF_LITE_FRONTEND=ON"
else
    CMAKE_OPTIONS+=" -DENABLE_OV_TF_LITE_FRONTEND=OFF"
fi

if grep -E -q "\bpytorch\b" <<< "${enable_frontends}" >/dev/null 2>&1; then
    CMAKE_OPTIONS+=" -DENABLE_OV_PYTORCH_FRONTEND=ON"
else
    CMAKE_OPTIONS+=" -DENABLE_OV_PYTORCH_FRONTEND=OFF"
fi

CMAKE_OPTIONS+=" -DENABLE_GAPI_PREPROCESSING=ON"
CMAKE_OPTIONS+=" -DENABLE_IR_V7_READER=OFF"
CMAKE_OPTIONS+=" -DENABLE_STRICT_DEPENDENCIES=OFF"
################################################
### Build type
CMAKE_OPTIONS+=" -DCMAKE_BUILD_TYPE=$build_type"
################################################

### Python API

if [ "$enable_python" = true ]; then
    CMAKE_OPTIONS+=" -DENABLE_PYTHON=ON"
else
    CMAKE_OPTIONS+=" -DENABLE_PYTHON=OFF"
fi

################################################
CMAKE_OPTIONS+=" -DENABLE_OPENCV=OFF"

### Code coverage (gcov)
# CMAKE_OPTIONS+=" -DENABLE_COVERAGE=ON"
################################################

################################################
if [ "$use_ccache" = true ]; then
    export CCACHE_DIR=$HOME/.ccache
    export CCACHE_MAXSIZE=50G
    CMAKE_OPTIONS+=" -DCMAKE_CXX_COMPILER_LAUNCHER=ccache"
fi

#-DCMAKE_LINKER=/path/to/linker

if [ "$arm" = true ]; then
    enable_mold=false
    export CCACHE_DIR=$HOME/.ccache_arm
    CMAKE_OPTIONS+=" -DCMAKE_TOOLCHAIN_FILE=cmake/arm64.toolchain.cmake"
    # CMAKE_OPTIONS+=" -DCMAKE_TOOLCHAIN_FILE=cmake/aarch64_clang.toolchain.cmake"
    # CMAKE_OPTIONS+=" -DTHREADING=SEQ"
    ### Static build for easier debug in emulator
    # CMAKE_OPTIONS+=" -DBUILD_SHARED_LIBS=OFF"
    # CMAKE_OPTIONS+=" -DENABLE_ARM_COMPUTE_CMAKE=ON"
fi

if [ "$arm32" = true ]; then
    enable_mold=false
    export CCACHE_DIR=$HOME/.ccache_arm32
    CMAKE_OPTIONS+=" -DCMAKE_TOOLCHAIN_FILE=cmake/arm.toolchain.cmake"
    # CMAKE_OPTIONS+=" -DCMAKE_TOOLCHAIN_FILE=cmake/aarch64_clang.toolchain.cmake"
    # CMAKE_OPTIONS+=" -DTHREADING=SEQ"
    ### Static build for easier debug in emulator
    # CMAKE_OPTIONS+=" -DBUILD_SHARED_LIBS=OFF"
    # CMAKE_OPTIONS+=" -DENABLE_ARM_COMPUTE_CMAKE=ON"
fi

if [ "$riscv" = true ]; then
    enable_mold=false
    export CCACHE_DIR=$HOME/.ccache_riscv
    CMAKE_OPTIONS+=" -DRISCV_TOOLCHAIN_ROOT=/opt/riscv"
    CMAKE_OPTIONS+=" -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/riscv64-100-xuantie-gnu.toolchain.cmake"
    # CMAKE_OPTIONS+=" -DCMAKE_TOOLCHAIN_FILE=cmake/aarch64_clang.toolchain.cmake"
    # CMAKE_OPTIONS+=" -DTHREADING=SEQ"
    ### Static build for easier debug in emulator
    # CMAKE_OPTIONS+=" -DBUILD_SHARED_LIBS=OFF"
    # CMAKE_OPTIONS+=" -DENABLE_ARM_COMPUTE_CMAKE=ON"
fi

# if not riscv and not arm
# if [ "$arm" = false ] && [ "$riscv" = false ] && [ "$arm32" = false ]; then
#     CMAKE_OPTIONS+=" -DCMAKE_C_FLAGS=-Wno-deprecated"
#     CMAKE_OPTIONS+=" -DCMAKE_C_FLAGS=-Wno-deprecated-declarations"
#     CMAKE_OPTIONS+=" -DCMAKE_C_FLAGS=-Wno-abi"
#     CMAKE_OPTIONS+=" -DCMAKE_CXX_FLAGS=-Wno-abi"
# fi

if [ "$enable_debug_caps" = true ]; then
    CMAKE_OPTIONS+=" -DENABLE_DEBUG_CAPS=ON"
    CMAKE_OPTIONS+=" -DENABLE_CPU_DEBUG_CAPS=ON"
else
    suffix+="_no_debug_caps"
fi

if [ "$enable_openvino_debug" = true ]; then
    CMAKE_OPTIONS+=" -DENABLE_OPENVINO_DEBUG=ON"
    suffix="${suffix}_ov_debug"
fi

if [ "$verbose" -gt 0 ]; then
    CMAKE_OPTIONS+=" -DCMAKE_RULE_MESSAGES=ON"
    CMAKE_OPTIONS+=" -DCMAKE_TARGET_MESSAGES=ON"
    cmake_log_level="DEBUG"
else
    CMAKE_OPTIONS+=" -DCMAKE_RULE_MESSAGES=OFF"
    CMAKE_OPTIONS+=" -DCMAKE_TARGET_MESSAGES=OFF"
    cmake_log_level="ERROR"
fi

if [ "$enable_clang_format" = true ]; then
    CMAKE_OPTIONS+=" -DENABLE_CLANG_FORMAT=ON"
else
    CMAKE_OPTIONS+=" -DENABLE_CLANG_FORMAT=OFF"
fi

if [ "$enable_clang_tidy" = true ]; then
    CMAKE_OPTIONS+=" -DENABLE_CLANG_TIDY=ON"
else
    CMAKE_OPTIONS+=" -DENABLE_CLANG_TIDY=OFF"
fi

if [ "$mlir" = true ]; then
    CMAKE_OPTIONS+=" -DENABLE_MLIR_FOR_CPU=ON"
    # CMAKE_OPTIONS+=" -DLIBCXX_USE_COMPILER_RT=ON"
    # clang_version="18"
fi

if [[ -n "$clang_version" ]]; then
    export CC=/usr/bin/clang-"${clang_version}"
    export CXX=/usr/bin/clang++-"${clang_version}"
    # export CXXFLAGS="${CXXFLAGS} -Wunused-private-field"
fi

if [ "$enable_mold" = true ]; then
    CMAKE_OPTIONS+=" -DCMAKE_EXE_LINKER_FLAGS='-fuse-ld=mold' -DCMAKE_SHARED_LINKER_FLAGS='-fuse-ld=mold' -DCMAKE_MODULE_LINKER_FLAGS='-fuse-ld=mold'"
fi

# if [ "$enable_itt_collect" = true ]; then
#     CMAKE_OPTIONS+=" -DENABLE_PROFILING_ITT=ON"
#     CMAKE_OPTIONS+=" -DSELECTIVE_BUILD=COLLECT"
#     suffix="${suffix}_itt_collect"
# fi

# CMAKE_OPTIONS+=" -DENABLE_SNIPPETS_LIBXSMM_TPP=ON"
CMAKE_OPTIONS+=" -DENABLE_MLAS_FOR_CPU=ON"
CMAKE_OPTIONS+=" -DENABLE_KLEIDIAI_FOR_CPU=ON"

CMAKE_OPTIONS+=" -DENABLE_MLAS_FOR_CPU_DEFAULT=ON"
CMAKE_OPTIONS+=" -DENABLE_CPU_SPECIFIC_TARGET_PER_TEST=ON"
# CMAKE_OPTIONS+=" -DENABLE_CPU_SUBSET_TESTS_PATH='custom/single_layer_tests/classes/eltwise.cpp custom/single_layer_tests/instances/common/eltwise.cpp custom/single_layer_tests/instances/x64/eltwise.cpp'"
CMAKE_OPTIONS+=" -DENABLE_CPU_SUBSET_TESTS_PATH=shared_tests_instances/subgraph_tests/conv_eltwise_fusion.cpp"
#CMAKE_OPTIONS+=" -DENABLE_CPU_SUBSET_TESTS_PATH='custom/subgraph_tests/src/classes/conv_maxpool_activ.cpp custom/subgraph_tests/src/common/conv_maxpool_activ.cpp'"
# CMAKE_OPTIONS+=" -DENABLE_CPU_SUBSET_TESTS_PATH='custom/subgraph_tests/src/common/merge_transpose_reorder.cpp'"
#CMAKE_OPTIONS+=" -DENABLE_CPU_SUBSET_TESTS_PATH='shared_tests_instances/single_layer_tests/fake_quantize.cpp'"
# CMAKE_OPTIONS+=" -DENABLE_CPU_SUBSET_TESTS_PATH='custom/subgraph_tests/src/common/eltwise_chain.cpp custom/subgraph_tests/src/classes/eltwise_chain.hpp'"
# MAKE_OPTIONS+=" -DENABLE_CPU_SUBSET_TESTS_PATH='custom/subgraph_tests/src/common/eltwise_chain.cpp'"
# CMAKE_OPTIONS+=" -DCMAKE_RULE_MESSAGES=ON"
CMAKE_OPTIONS+=" -DENABLE_NCC_STYLE=OFF"
# cpplint
CMAKE_OPTIONS+=" -DENABLE_CPPLINT=ON"
CMAKE_OPTIONS+=" -DCMAKE_COLOR_DIAGNOSTICS=OFF"
CMAKE_OPTIONS+=" -DCMAKE_COMPILE_WARNING_AS_ERROR=ON"
# Disable anoying deprecated warnings
export CXXFLAGS="${CXXFLAGS} -Wno-deprecated-declarations -Wno-deprecated"
# CMAKE_CXX_FLAGS="-Wunused-private-field -Wno-deprecated-declarations -Wsign-compare"
# CMAKE_OPTIONS+=" -DCMAKE_CXX_FLAGS=\"${CMAKE_CXX_FLAGS}\""
#CMAKE_OPTIONS+=" -DCMAKE_CXX_FLAGS=\"-Wno-deprecated-declarations -Wsign-compare -Wunused-private-field\""
################################################
CMAKE_OPTIONS+=" -DCMAKE_EXPORT_COMPILE_COMMANDS=1"
CMAKE_OPTIONS+=" -DCMAKE_VERIFY_INTERFACE_HEADER_SETS=ON"
CMAKE_OPTIONS+=" -DCMAKE_INSTALL_PREFIX=${PWD}/install"

### Static build
# CMAKE_OPTIONS+=" -DBUILD_SHARED_LIBS=OFF"

# CI cmake options
# CMAKE_OPTIONS=" -DENABLE_LTO=ON -DVERBOSE_BUILD=ON -DCMAKE_BUILD_TYPE=$build_type -DENABLE_PYTHON=ON -DBUILD_SHARED_LIBS=OFF -DENABLE_ONEDNN_FOR_GPU=OFF -DPYTHON_EXECUTABLE=/usr/bin/python3.8 -DENABLE_TESTS=ON -DENABLE_OV_ONNX_FRONTEND=ON -DENABLE_FASTER_BUILD=ON -DENABLE_STRICT_DEPENDENCIES=OFF -DENABLE_REQUIREMENTS_INSTALL=OFF"

################################################
# CMAKE_OPTIONS+=" -DCMAKE_VERBOSE_MAKEFILE=ON"
# CMAKE_OPTIONS+=" -DENABLE_CLANG_FORMAT=OFF"
# CMAKE_OPTIONS+=" -DTBB_DIR=./temp/tbb/cmake/"

if [[ "$enable_cc" == "collect" ]]; then
    # shellcheck source=/dev/null
    source "${current_dir}"/build_templates/openvino-lin-cc-collect
    build_dir="build_openvino-lin-cc-collect"
elif [[ "$enable_cc" = "apply" ]]; then
    # shellcheck source=/dev/null
    source "${current_dir}"/build_templates/openvino-lin-cc
    build_dir="build_openvino-lin-cc"
fi

if [ "$use_ccache" = true ]; then
    export CCACHE_DIR="${CCACHE_DIR}_${build_type}"
fi

build_dir=build_${build_type}
build_dir="${build_dir}${suffix}"

if [ "$arm" = true ]; then
    build_dir="${build_dir}_arm"
fi

if [ "$arm32" = true ]; then
    build_dir="${build_dir}_arm32"
fi

if [ "$riscv" = true ]; then
    build_dir="${build_dir}_riscv"
fi

mkdir -p "${build_dir}"

export CLICOLOR=0

num_of_build_jobs=$(($(nproc --all)-2))

if [ "$verbose" -gt 0 ]; then
    echo "build directory: $build_dir"
    echo "OS: $OS"
    echo "CFLAGS: $CFLAGS"
    echo "CXXFLAGS: $CXXFLAGS"
    echo "CMAKE_OPTIONS: $CMAKE_OPTIONS"
    echo "targets: $target"
    echo "num jobs: $num_of_build_jobs"
fi

if [ "$generate" = true ]; then
    echo "Gerenerating cache ..."
    eval cmake --log-level="$cmake_log_level" "${CMAKE_OPTIONS}" ./ -B "${build_dir}"
    # cmake --trace-expand --log-level=TRACE ${CMAKE_OPTIONS} ./ -B ${build_dir}
fi

if [ "$configure_only" = true ]; then
    exit 0
fi

# num_of_build_jobs=9
echo "Building ..."
cmake --build "${build_dir}" --target "${target}" --parallel ${num_of_build_jobs}
