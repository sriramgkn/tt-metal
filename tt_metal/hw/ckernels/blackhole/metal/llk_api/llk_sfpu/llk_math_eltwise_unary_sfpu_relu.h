// SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.
//
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include "ckernel_sfpu_relu.h"
#include "llk_math_eltwise_unary_sfpu_1_param.h"
#include "llk_math_eltwise_unary_sfpu_init.h"

namespace ckernel {

// New LLK SFPU APIs

template <bool APPROXIMATE>
inline void llk_math_eltwise_unary_sfpu_relu_init() {
    llk_math_eltwise_unary_sfpu_init<SfpuType::relu_min, APPROXIMATE>();
}

template <bool APPROXIMATE>
inline void llk_math_eltwise_unary_sfpu_lrelu_init() {
    llk_math_eltwise_unary_sfpu_init<SfpuType::lrelu, APPROXIMATE>();
}
template <bool APPROXIMATE>
inline void llk_math_eltwise_unary_sfpu_relu_max_init() {
    llk_math_eltwise_unary_sfpu_init<SfpuType::relu_max, APPROXIMATE>();
}

template <bool APPROXIMATE>
inline void llk_math_eltwise_unary_sfpu_relu_min_init() {
    llk_math_eltwise_unary_sfpu_init<SfpuType::relu_min, APPROXIMATE>();
}

template <bool APPROXIMATE>
inline void llk_math_eltwise_unary_sfpu_lrelu(uint dst_index, uint param0 = 0) {
    llk_math_eltwise_unary_sfpu_1_param<APPROXIMATE>(
        ckernel::sfpu::calculate_lrelu<APPROXIMATE>,
        ckernel::sfpu::calculate_lrelu<APPROXIMATE>,
        dst_index,
        (int)VectorMode::RC,
        param0);
}

template <bool APPROXIMATE>
inline void llk_math_eltwise_unary_sfpu_relu_max(uint dst_index, uint param0 = 0) {
    llk_math_eltwise_unary_sfpu_1_param<APPROXIMATE>(
        ckernel::sfpu::relu_max<APPROXIMATE>,
        ckernel::sfpu::relu_max<APPROXIMATE>,
        dst_index,
        (int)VectorMode::RC,
        param0);
}

template <bool APPROXIMATE>
inline void llk_math_eltwise_unary_sfpu_relu_min(uint dst_index, uint param0 = 0) {
    llk_math_eltwise_unary_sfpu_1_param<APPROXIMATE>(
        ckernel::sfpu::relu_min<APPROXIMATE>,
        ckernel::sfpu::relu_min<APPROXIMATE>,
        dst_index,
        (int)VectorMode::RC,
        param0);
}

template <bool APPROXIMATE>
inline void llk_math_eltwise_unary_sfpu_relu(uint dst_index) {
    llk_math_eltwise_unary_sfpu_1_param<APPROXIMATE>(
        ckernel::sfpu::relu_min<APPROXIMATE>, ckernel::sfpu::relu_min<APPROXIMATE>, dst_index, (int)VectorMode::RC, 0);
}

}  // namespace ckernel