# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.

# SPDX-License-Identifier: Apache-2.0

import torch
import pytest
import ttnn
from tests.ttnn.unit_tests.operations.backward.utility_funcs import data_gen_with_range, compare_pcc


@pytest.mark.skip(reason="this test is failing because ttnn.eq_bw doesn't have a corresponding API call")
@pytest.mark.parametrize(
    "input_shapes",
    (
        (torch.Size([1, 1, 32, 32])),
        (torch.Size([1, 1, 320, 384])),
        (torch.Size([1, 3, 320, 384])),
    ),
)
def test_bw_binary_eq(input_shapes, device):
    in_data, input_tensor = data_gen_with_range(input_shapes, -100, 100, device, True)
    other_data, other_tensor = data_gen_with_range(input_shapes, -90, 100, device, True)
    grad_data, grad_tensor = data_gen_with_range(input_shapes, -20, 40, device)

    tt_output_tensor_on_device = ttnn.eq_bw(grad_tensor, input_tensor, other_tensor)
    golden_function = ttnn.get_golden_function(ttnn.eq_bw)
    golden_tensor = golden_function(grad_data, in_data, other_data)
    comp_pass = compare_pcc(tt_output_tensor_on_device, golden_tensor)
    assert comp_pass


@pytest.mark.skip(reason="this test is failing because ttnn.eq_bw doesn't have a corresponding API call")
@pytest.mark.parametrize(
    "input_shapes",
    (
        (torch.Size([1, 1, 32, 32])),
        (torch.Size([1, 1, 320, 384])),
        (torch.Size([1, 3, 320, 384])),
    ),
)
@pytest.mark.parametrize("are_required_outputs", [[True, True], [True, False], [False, True], [False, False]])
def test_bw_binary_eq_opt_output(input_shapes, device, are_required_outputs):
    in_data, input_tensor = data_gen_with_range(input_shapes, -100, 100, device, True)
    other_data, other_tensor = data_gen_with_range(input_shapes, -90, 100, device, True)
    grad_data, grad_tensor = data_gen_with_range(input_shapes, -20, 40, device)
    input_grad = None
    other_grad = None
    if are_required_outputs[0]:
        _, input_grad = data_gen_with_range(input_shapes, -1, 1, device)
    if are_required_outputs[1]:
        _, other_grad = data_gen_with_range(input_shapes, -1, 1, device)

    tt_output_tensor_on_device = ttnn.eq_bw(
        grad_tensor,
        input_tensor,
        other_tensor,
        are_required_outputs=are_required_outputs,
        input_a_grad=input_grad,
        input_b_grad=other_grad,
    )

    golden_function = ttnn.get_golden_function(ttnn.eq_bw)
    golden_tensor = golden_function(grad_data, in_data, other_data)

    status = True
    for i in range(len(are_required_outputs)):
        if are_required_outputs[i]:
            status = status & compare_pcc([tt_output_tensor_on_device[i]], [golden_tensor[i]])
    assert status


@pytest.mark.skip(reason="this test is failing because ttnn.eq_bw doesn't have a corresponding API call")
@pytest.mark.parametrize(
    "input_shapes",
    (
        (torch.Size([1, 1, 32, 32])),
        (torch.Size([1, 1, 320, 384])),
    ),
)
@pytest.mark.parametrize("are_required_outputs", [[True, True], [True, False], [False, True], [False, False]])
def test_bw_binary_eq_opt_output_qid(input_shapes, device, are_required_outputs):
    in_data, input_tensor = data_gen_with_range(input_shapes, -100, 100, device, True)
    other_data, other_tensor = data_gen_with_range(input_shapes, -90, 100, device, True)
    grad_data, grad_tensor = data_gen_with_range(input_shapes, -20, 40, device)
    input_grad = None
    other_grad = None

    if are_required_outputs[0]:
        _, input_grad = data_gen_with_range(input_shapes, -1, 1, device)
    if are_required_outputs[1]:
        _, other_grad = data_gen_with_range(input_shapes, -1, 1, device)

    cq_id = 0

    tt_output_tensor_on_device = ttnn.eq_bw(
        grad_tensor,
        input_tensor,
        other_tensor,
        are_required_outputs=are_required_outputs,
        input_a_grad=input_grad,
        input_b_grad=other_grad,
        queue_id=cq_id,
    )

    golden_function = ttnn.get_golden_function(ttnn.eq_bw)
    golden_tensor = golden_function(grad_data, in_data, other_data)

    status = True
    for i in range(len(are_required_outputs)):
        if are_required_outputs[i]:
            status = status & compare_pcc([tt_output_tensor_on_device[i]], [golden_tensor[i]])
    assert status


@pytest.mark.parametrize(
    "input_shapes",
    (
        (torch.Size([1, 1, 32, 32])),
        (torch.Size([1, 1, 320, 384])),
        (torch.Size([1, 3, 320, 384])),
    ),
)
@pytest.mark.parametrize("other", [1.0])
def test_bw_unary_eq(input_shapes, other, device):
    in_data, input_tensor = data_gen_with_range(input_shapes, -100, 100, device, True)
    grad_data, grad_tensor = data_gen_with_range(input_shapes, -100, 100, device)

    tt_output_tensor_on_device = ttnn.eq_bw(grad_tensor, input_tensor, other)
    golden_function = ttnn.get_golden_function(ttnn.eq_bw)
    golden_tensor = golden_function(grad_data, in_data, other)
    comp_pass = compare_pcc(tt_output_tensor_on_device, golden_tensor)
    assert comp_pass
