# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.

# SPDX-License-Identifier: Apache-2.0

import ttnn
import torch
import torch.nn as nn
from models.demos.wormhole.mistral7b.tt.mistral_decoder import TtTransformerBlock
from models.demos.wormhole.mistral7b.tt.mistral_rms_norm import TtRMSNorm
import ttnn
from typing import Optional


class TtTransformer(nn.Module):
    def __init__(
        self,
        args,
        dtype,
        device,
        state_dict,
        weight_cache_path,
        layers,
        rot_mat,
        start_pos,
    ):
        super().__init__()
        self.args = args
        self.vocab_size = args.vocab_size
        self.n_layers = args.n_layers
        self.start_pos = start_pos
        self.device = device
        assert self.vocab_size > 0

        self.layers = torch.nn.ModuleList(
            [
                TtTransformerBlock(
                    args=args,
                    device=device,
                    dtype=dtype,
                    state_dict=state_dict,
                    weight_cache_path=weight_cache_path,
                    layer_num=i,
                    rot_mat=rot_mat,
                    start_pos=start_pos,
                )
                for i in layers
            ]
        )
        self.norm = TtRMSNorm(
            device=device,
            state_dict=state_dict,
            weight_cache_path=weight_cache_path,
            dtype=dtype,
            layer_num=None,
            weight_key="norm",
        )
        self.state_dict = state_dict

        self.output_weight = ttnn.as_tensor(
            self.state_dict["output.weight"].permute(1, 0),
            device=device,
            layout=ttnn.TILE_LAYOUT,
            dtype=dtype,
            memory_config=ttnn.DRAM_MEMORY_CONFIG,
            cache_file_name=weight_cache_path / "output.weight",
        )
        self.output_program_config = ttnn.operations.matmul.create_matmul_1d_systolic_array_program_config(
            input_shape_a=ttnn.Shape([1, 1, args.max_batch_size, args.dim]),
            input_shape_b=self.output_weight.shape,
            core_grid=args.max_grid_size,
            fp32_dst=args.get_compute_kernel_config().fp32_dest_acc_en,
        )

    def forward(
        self,
        x: ttnn.Tensor,
        current_pos: int,
        attn_masks: Optional[ttnn.Tensor] = None,
    ):
        for layer in self.layers:
            x = layer(x, current_pos, attn_masks)

        x = self.norm(x)
        output = ttnn.linear(
            x,
            self.output_weight,
            program_config=self.output_program_config,
            compute_kernel_config=self.args.get_compute_kernel_config(),
        )
        return output