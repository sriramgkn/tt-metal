#include "tt_dnn/op_library/bcast/bcast_op.hpp"
#include "tt_dnn/op_library/work_split.hpp"
#include "tensor/tensor.hpp"
#include "tt_metal/host_api.hpp"

#include "tt_metal/common/constants.hpp"
#include "tt_metal/detail/util.hpp"


using namespace tt::tt_metal;
using namespace tt::constants;


namespace tt {

namespace tt_metal {

operation::ProgramWithCallbacks bcast_multi_core_hw(const Tensor &a, const Tensor &b, Tensor& output, BcastOpMath::Enum bcast_math, BcastOpDim::Enum bcast_dim) {
    TT_ASSERT(bcast_dim == BcastOpDim::HW);

    const auto ashape = a.shape();
    const auto bshape = b.shape();
    uint32_t N  = ashape[0], C  = ashape[1], H  = ashape[2], W  = ashape[3];
    uint32_t bN = bshape[0], bC = bshape[1], bH = bshape[2], bW = bshape[3];
    uint32_t NC = N*C;
    uint32_t HW = H*W;

    uint32_t Wt = W/TILE_WIDTH;
    uint32_t Ht = H/TILE_HEIGHT;

    uint32_t num_tensor_tiles = NC*Ht*Wt;
    uint32_t num_btensor_tiles = NC*bH*bW / TILE_HW;

	uint32_t bnc1 = (bN*bC == 1) ? 1 : 0;

    tt_metal::Program program = tt_metal::Program();

    tt_metal::Device *device = a.device();

	tt::DataFormat cb_data_format = tt_metal::datatype_to_dataformat_converter(a.dtype());

    uint32_t single_tile_size = tt_metal::detail::TileSize(cb_data_format);


    auto compute_and_storage_grid_size = device->compute_and_storage_grid_size();
    uint32_t num_cores_x = compute_and_storage_grid_size.x;
    uint32_t num_cores_y = compute_and_storage_grid_size.y;
    auto [num_cores, all_cores, core_group_1, core_group_2, num_tiles_per_core_group_1, num_tiles_per_core_group_2] = split_work_to_cores(compute_and_storage_grid_size, num_tensor_tiles);

	auto src0_buffer = a.buffer();
	auto src1_buffer = b.buffer();
	auto dst_buffer = output.buffer();
	TT_ASSERT(dst_buffer != nullptr, "Output buffer should be allocated on device!");

    const char* reader_name = bcast_op_utils::get_reader_name(bcast_dim, BcastOpParallelizationStrategy::MULTI_CORE_HW);
    const char* compute_name = bcast_op_utils::get_compute_name(bcast_dim);

	uint32_t src0_cb_index = 0;
	uint32_t num_input_tiles = 2;
	auto cb_src0 = tt_metal::CreateCircularBuffers(
		program,
		src0_cb_index,
		all_cores,
		num_input_tiles,
		num_input_tiles * single_tile_size,
		cb_data_format
	);

	uint32_t src1_cb_index = 1;
	auto cb_src1 = tt_metal::CreateCircularBuffers(
		program,
		src1_cb_index,
		all_cores,
		num_input_tiles,
		num_input_tiles * single_tile_size,
		cb_data_format
	);

	uint32_t output_cb_index = 16; // output operands start at index 16
	uint32_t num_output_tiles = 2;
	auto cb_output = tt_metal::CreateCircularBuffers(
		program,
		output_cb_index,
		all_cores,
		num_output_tiles,
		num_output_tiles * single_tile_size,
		cb_data_format
	);

	bool src0_is_dram = src0_buffer->buffer_type() == tt_metal::BufferType::DRAM ? 1 : 0;
    bool src1_is_dram = src1_buffer->buffer_type() == tt_metal::BufferType::DRAM ? 1 : 0;
    std::vector<uint32_t> reader_compile_time_args = {static_cast<uint32_t>(cb_data_format), (uint32_t)src0_is_dram, (uint32_t)src1_is_dram};

    bool dst_is_dram = dst_buffer->buffer_type() == tt_metal::BufferType::DRAM ? 1 : 0;
    std::vector<uint32_t> writer_compile_time_args = {
        (std::uint32_t) output_cb_index,
        static_cast<uint32_t>(cb_data_format),
        (std::uint32_t) dst_is_dram
    };

	tt_metal::DataMovementKernel *binary_reader_kernel = tt_metal::CreateDataMovementKernel(
		program,
		reader_name,
		all_cores,
		reader_compile_time_args,
		tt_metal::DataMovementProcessor::RISCV_1,
		tt_metal::NOC::RISCV_1_default);

	tt_metal::DataMovementKernel *unary_writer_kernel = tt_metal::CreateDataMovementKernel(
		program,
		"tt_metal/kernels/dataflow/writer_unary_interleaved_start_id.cpp",
		all_cores,
		writer_compile_time_args,
		tt_metal::DataMovementProcessor::RISCV_0,
		tt_metal::NOC::RISCV_0_default);

	// TODO(AP): add dimensions and op params
	// Ignore Ht and just read num_tiles_per_core
	vector<uint32_t> compute_kernel_args_group_1 = {
		1, // B
		1, // Ht
		num_tiles_per_core_group_1  // Wt
	};

	bool fp32_dest_acc_en = false;
	bool math_approx_mode = false;
	auto bcast_kernel_group_1 = tt_metal::CreateComputeKernel(
		program,
		compute_name,
		core_group_1,
		compute_kernel_args_group_1,
		MathFidelity::HiFi4,
		fp32_dest_acc_en,
		math_approx_mode
	);
	bcast_op_utils::add_defines(bcast_kernel_group_1, bcast_dim, bcast_math);
	if (!core_group_2.ranges().empty()) {
		// TODO(AP): add dimensions and op params
		// Ignore Ht and just read num_tiles_per_core
		vector<uint32_t> compute_kernel_args_group_2 = {
			1, // B
			1, // Ht
			num_tiles_per_core_group_2  // Wt
		};
		auto bcast_kernel_group_2 = tt_metal::CreateComputeKernel(
			program,
			compute_name,
			core_group_2,
			compute_kernel_args_group_2,
			MathFidelity::HiFi4,
			fp32_dest_acc_en,
			math_approx_mode
		);
		bcast_op_utils::add_defines(bcast_kernel_group_2, bcast_dim, bcast_math);
	}

	for (uint32_t i = 0, num_tiles_read = 0; i < num_cores; i++){
		CoreCoord core = {i / num_cores_y, i % num_cores_y};
		uint32_t num_tensor_tiles_per_core;
		if (core_group_1.core_coord_in_core_ranges(core)) {
			num_tensor_tiles_per_core = num_tiles_per_core_group_1;
		} else if (core_group_2.core_coord_in_core_ranges(core)) {
			num_tensor_tiles_per_core = num_tiles_per_core_group_2;
		} else {
			TT_ASSERT(false, "Core not in specified core ranges");
		}

		tt_metal::SetRuntimeArgs(
			binary_reader_kernel,
			core,
			{
				a.buffer()->address(), // 0
				0, // 1
				0, // 2
				num_tensor_tiles_per_core, // 3
				b.buffer()->address(), // 4
				0, // 5
				0, // 6
				num_btensor_tiles, // 7
				num_tensor_tiles_per_core, // 8
				1, // 9
				1, // 10
				num_tensor_tiles_per_core, // 11
				bnc1, // 12
				num_tiles_read, // 13
				Ht*Wt, // 14
			}
		);

		tt_metal::SetRuntimeArgs(
			unary_writer_kernel, core,
			{
				output.buffer()->address(),
				num_tensor_tiles_per_core,
				num_tiles_read,
			}
		);
		num_tiles_read += num_tensor_tiles_per_core;
	}

    auto override_runtime_args_callback = [
            binary_reader_kernel,
            unary_writer_kernel,
            num_cores,
            num_cores_y
        ]
    (
        const std::vector<Buffer*>& input_buffers,
        const std::vector<Buffer*>& output_buffers
    ) {

        auto src_dram_buffer_a = input_buffers.at(0);

        auto src_dram_buffer_b = input_buffers.at(1);

        auto dst_dram_buffer = output_buffers.at(0);

        for (uint32_t i = 0, num_tiles_written = 0; i < num_cores; i++){
            CoreCoord core = {i / num_cores_y, i % num_cores_y};

            {
                auto runtime_args = GetRuntimeArgs(binary_reader_kernel, core);
                runtime_args[0] = src_dram_buffer_a->address();
                runtime_args[4] = src_dram_buffer_b->address();
                SetRuntimeArgs(binary_reader_kernel, core, runtime_args);
            }

            {
                auto runtime_args = GetRuntimeArgs(unary_writer_kernel, core);
                runtime_args[0] = dst_dram_buffer->address();
                SetRuntimeArgs(unary_writer_kernel, core, runtime_args);
            }
        }
    };

    return {std::move(program), override_runtime_args_callback};
}

}  // namespace tt_metal

}  // namespace tt
