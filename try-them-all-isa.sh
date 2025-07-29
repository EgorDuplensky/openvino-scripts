#!/usr/bin/env bash

command_to_execute="$*"

isa_list="SSE41
AVX
AVX2
AVX2_VNNI
AVX512_CORE
AVX512_CORE_VNNI
AVX512_CORE_BF16
AVX512_CORE_FP16
AVX512_CORE_AMX
AVX2_VNNI_2
AVX512_CORE_AMX_FP16"

for isa in $isa_list; do
    printf "### Running with '%s' ISA... " $isa
    if ONEDNN_MAX_CPU_ISA=$isa $command_to_execute; then
        printf "OK!\n"
    else
        printf "FAILED!\n"
    fi
done
