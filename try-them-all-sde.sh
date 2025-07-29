#!/usr/bin/env bash

command_to_execute="$*"
sde_bin=sde
log_file=/tmp/try-them-all-sde.log

arch_list="
slm
glm
glp
tnt
snr
skl
cnl
icl
skx
clx
cpx
icx
tgl
adl
spr
gnr
srf
"

true > "$log_file"

for arch in ${arch_list}; do
    printf "### Running with '%s' architecture... " $arch
    if $sde_bin -"${arch}" -- $command_to_execute >> "$log_file"; then
        printf "OK!\n"
    else
        printf "FAILED!\n"
    fi
done
