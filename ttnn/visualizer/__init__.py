# SPDX-FileCopyrightText: © 2023 Tenstorrent Inc.

# SPDX-License-Identifier: Apache-2.0

import math

from bokeh.embed import components
from bokeh.models import Plot, ColumnDataSource, LinearAxis, CustomJSTickFormatter, NumeralTickFormatter, Rect, Range1d
from bokeh.models.tools import WheelZoomTool, PanTool, ResetTool, ZoomInTool, ZoomOutTool, HoverTool
from bokeh.palettes import Category20
from bokeh.plotting import figure
from flask import Flask, render_template
from loguru import logger
import numpy as np
import pandas as pd

import ttnn
import ttnn.database

ttnn.CONFIG.enable_logging = False
ttnn.CONFIG.delete_reports_on_start = False

logger.info(f"Visualizer ttnn.CONFIG {ttnn.CONFIG}")

BUFFER_TO_COLOR_INDEX = {}
COLORS = Category20[20]


def shorten_stack_trace(stack_trace, num_lines=12):
    if stack_trace is None:
        return None
    stack_trace = stack_trace.split("\n")[:num_lines]
    stack_trace = "\n".join(stack_trace)
    return stack_trace


def red_to_green_spectrum(percentage):
    percentage_difference = 1.0 - percentage
    red_color = int(min(255, percentage_difference * 8 * 255))
    green_color = int(min(255, percentage * 2 * 255))
    color = f"#{red_color:02X}{green_color:02X}{0:02X}"
    return color


def tensor_comparison_record_to_percentage(record):
    if record.matches:
        percentage = 1
    elif record.actual_pcc < 0:
        percentage = 0
    elif record.actual_pcc >= record.desired_pcc:
        return 1.0
    else:
        percentage = record.actual_pcc * 0.9 / record.desired_pcc
    return percentage


def comparison_percentages(table_name, operation_id):
    output_tensor_records = ttnn.database.query_output_tensors(operation_id=operation_id)
    output_tensor_records = sorted(output_tensor_records, key=lambda tensor: tensor.output_index)

    if not output_tensor_records:
        return "No output tensors"

    percentages = []
    for output_tensor_record in output_tensor_records:
        tensor_comparison_record = ttnn.database.query_tensor_comparison_record(
            table_name, tensor_id=output_tensor_record.tensor_id
        )
        if tensor_comparison_record is None:
            continue
        if tensor_comparison_record.matches:
            percentages.append(1)
        else:
            percentages.append(tensor_comparison_record.actual_pcc / tensor_comparison_record.desired_pcc)

    if not percentages:
        return "Couldn't compare (Does the operation have a golden function?)"

    percentage = sum(percentages) / len(percentages)
    return f"{percentage:.6f}"


def comparison_color(table_name, operation_id):
    output_tensor_records = ttnn.database.query_output_tensors(operation_id=operation_id)
    output_tensor_records = sorted(output_tensor_records, key=lambda tensor: tensor.output_index)

    if not output_tensor_records:
        return "white"

    percentages = []
    for output_tensor_record in output_tensor_records:
        tensor_comparison_record = ttnn.database.query_tensor_comparison_record(
            table_name, tensor_id=output_tensor_record.tensor_id
        )
        if tensor_comparison_record is None:
            continue
        percentages.append(tensor_comparison_record_to_percentage(tensor_comparison_record))

    if not percentages:
        return "grey"

    percentage = sum(percentages) / len(percentages)
    return red_to_green_spectrum(percentage)


app = Flask(__name__)


@app.route("/")
def root():
    return operations()


@app.route("/apis")
def apis():
    apis = ttnn.query_operations(include_experimental=True)
    df = pd.DataFrame(apis)
    df.sort_values(by=["is_experimental", "is_cpp_function", "name"], inplace=True)
    df["has_fallback"] = df["golden_function"].apply(lambda golden_function: golden_function is not None)
    df["will_fallback"] = df[["has_fallback", "allow_to_fallback_to_golden_function_on_failure"]].apply(
        lambda row: row.has_fallback and row.allow_to_fallback_to_golden_function_on_failure, axis=1
    )
    return render_template(
        "apis.html",
        apis=df.to_html(
            index=False,
            justify="center",
            columns=["name", "is_cpp_function", "is_experimental", "has_fallback", "will_fallback"],
        ),
    )


@app.route("/operations")
def operations():
    operations = list(ttnn.database.query_operations())

    def load_underlying_operations(operation_id):
        try:
            operation_history = pd.read_csv(
                ttnn.CONFIG.reports_path / "operation_history" / f"{operation_id}.csv", index_col=False
            )

            def normalize_program_cache_hit(value):
                if value == "std::nullopt":
                    return ""
                else:
                    return "HIT" if value == 1 else "MISS"

            operation_history["program_cache"] = operation_history.program_cache_hit.apply(normalize_program_cache_hit)

            def normalize_program_hash(value):
                if value == "std::nullopt":
                    return ""
                else:
                    return value

            operation_history["program_hash"] = operation_history.program_hash.apply(normalize_program_hash)

            return operation_history.to_html(
                columns=["operation_name", "operation_type", "program_cache", "program_hash"],
                index=False,
                justify="center",
            )
        except Exception as e:
            logger.warning(e)
            return ""

    return render_template(
        "operations.html",
        operations=operations,
        comparison_color=comparison_color,
        comparison_percentages=comparison_percentages,
        load_underlying_operations=load_underlying_operations,
    )


@app.route("/operations_with_l1_buffer_report")
def operations_with_l1_buffer_report():
    operations = list(ttnn.database.query_operations())

    l1_reports = {}
    stack_traces = {}
    for operation in operations:
        l1_reports[operation.operation_id] = create_summarized_l1_buffer_plot(operation.operation_id)
        stack_trace = ttnn.database.query_stack_trace(operation_id=operation.operation_id)
        stack_traces[operation.operation_id] = shorten_stack_trace(stack_trace)

    return render_template(
        "operations_with_l1_buffer_report.html",
        operations=operations,
        l1_reports=l1_reports,
        stack_traces=stack_traces,
    )


def create_summarized_l1_buffer_plot(operation_id):
    glyph_y_location = 0
    glyph_height = 1

    buffers = list(ttnn.database.query_buffers(operation_id))
    if len(buffers) == 0:
        return "", "There are no L1 Buffers!"
    device_ids = set(buffer.device_id for buffer in buffers)
    if len(device_ids) != 1:
        return "", "Cannot visualize buffer plot for multiple devices!"
    device_id = device_ids.pop()
    device = ttnn.database.query_device_by_id(device_id)

    l1_size = device.worker_l1_size

    memory_glyph_y_location = [glyph_y_location]
    memory_glyph_x_location = [l1_size // 2]
    memory_height = [glyph_height]
    memory_width = [l1_size]
    memory_color = ["white"]
    memory_line_color = ["black"]

    memory_data_source = ColumnDataSource(
        dict(
            glyph_y_location=memory_glyph_y_location,
            glyph_x_location=memory_glyph_x_location,
            glyph_height=memory_height,
            glyph_width=memory_width,
            color=memory_color,
            line_color=memory_line_color,
        )
    )

    buffers_glyph_y_location = []
    buffers_glyph_x_location = []
    buffers_height = []
    buffers_width = []
    buffers_color = []
    buffers_max_size_per_bank = []
    buffers_address = []

    for buffer in ttnn.database.query_buffers(operation_id):
        if (buffer.device_id, buffer.address, buffer.buffer_type) not in BUFFER_TO_COLOR_INDEX:
            BUFFER_TO_COLOR_INDEX[(buffer.device_id, buffer.address, buffer.buffer_type)] = len(BUFFER_TO_COLOR_INDEX)

        buffers_address.append(buffer.address)
        buffers_max_size_per_bank.append(buffer.max_size_per_bank)
        buffers_glyph_y_location.append(glyph_y_location)
        buffers_glyph_x_location.append(buffer.address + buffer.max_size_per_bank // 2)
        buffers_height.append(glyph_height)
        buffers_width.append(buffer.max_size_per_bank)
        buffers_color.append(
            COLORS[BUFFER_TO_COLOR_INDEX[(buffer.device_id, buffer.address, buffer.buffer_type)] % len(COLORS)]
        )

    buffers_glyph_x_location = np.asarray(buffers_glyph_x_location)
    buffers_glyph_y_location = np.asarray(buffers_glyph_y_location)
    buffers_height = np.asarray(buffers_height)
    buffers_width = np.asarray(buffers_width)
    buffers_data_source = ColumnDataSource(
        dict(
            glyph_y_location=buffers_glyph_y_location,
            glyph_x_location=buffers_glyph_x_location,
            glyph_height=buffers_height,
            glyph_width=buffers_width,
            color=buffers_color,
            address=buffers_address,
            max_size_per_bank=buffers_max_size_per_bank,
        )
    )

    plot = Plot(title=None, width=800, height=100, min_border=0, toolbar_location="below")

    xaxis = LinearAxis()
    plot.x_range = Range1d(0, l1_size)
    plot.add_layout(xaxis, "below")
    plot.xaxis.axis_label = "L1 Address Space"
    plot.xaxis.formatter = NumeralTickFormatter(format="0000000")

    memory_glyph = Rect(
        y="glyph_y_location",
        x="glyph_x_location",
        height="glyph_height",
        width="glyph_width",
        line_color="line_color",
        fill_color="color",
    )
    plot.add_glyph(memory_data_source, memory_glyph)

    buffer_glyph = Rect(
        y="glyph_y_location",
        x="glyph_x_location",
        height="glyph_height",
        width="glyph_width",
        line_color="black",
        fill_color="color",
    )
    buffer_renderer = plot.add_glyph(buffers_data_source, buffer_glyph)

    plot.add_tools(
        WheelZoomTool(),
        PanTool(),
        ResetTool(),
        ZoomInTool(),
        ZoomOutTool(),
        HoverTool(
            renderers=[buffer_renderer],
            tooltips=[("Address", "@address"), ("Max Size Per Bank", "@max_size_per_bank")],
        ),
    )
    return components(plot)


def create_detailed_l1_buffer_plot(operation_id):
    buffers = list(ttnn.database.query_buffers(operation_id))
    device_ids = set(buffer.device_id for buffer in buffers)
    if len(buffers) == 0:
        return "", "There are no L1 Buffers!"
    if len(device_ids) != 1:
        return "", "Cannot visualize buffer plot for multiple devices!"
    device_id = device_ids.pop()
    device = ttnn.database.query_device_by_id(device_id)
    l1_size = device.worker_l1_size

    core_grid = [device.num_y_cores, device.num_x_cores]
    num_cores = math.prod(core_grid)
    core_glyph_height = 100
    core_glyph_width = 10
    core_glyph_y_offset = core_glyph_height + 20
    core_glyph_x_offset = core_glyph_width + 10

    cores_y = []
    cores_x = []
    cores_glyph_y_location = []
    cores_glyph_x_location = []
    for core_y in range(core_grid[0]):
        for core_x in range(core_grid[1]):
            cores_y.append(core_y)
            cores_x.append(core_x)
            cores_glyph_y_location.append(core_y * core_glyph_y_offset)
            cores_glyph_x_location.append(core_x * core_glyph_x_offset)

    cores_glyph_y_location = np.asarray(cores_glyph_y_location)
    cores_glyph_x_location = np.asarray(cores_glyph_x_location)
    cores_height = np.full((num_cores,), core_glyph_height)
    cores_width = np.full((num_cores,), core_glyph_width)
    cores_data_source = ColumnDataSource(
        dict(
            glyph_y_location=cores_glyph_y_location,
            glyph_x_location=cores_glyph_x_location,
            glyph_height=cores_height,
            glyph_width=cores_width,
            core_y=cores_y,
            core_x=cores_x,
        )
    )

    buffer_pages_glyph_y_location = []
    buffer_pages_glyph_x_location = []
    buffer_pages_height = []
    buffer_pages_width = []
    buffer_pages_color = []

    num_buffer_pages = 0
    for buffer_page in ttnn.database.query_buffer_pages(operation_id):
        if (buffer_page.device_id, buffer_page.address, buffer_page.buffer_type) not in BUFFER_TO_COLOR_INDEX:
            BUFFER_TO_COLOR_INDEX[(buffer_page.device_id, buffer_page.address, buffer_page.buffer_type)] = len(
                BUFFER_TO_COLOR_INDEX
            )

        buffer_page_glyph_y_location = buffer_page.core_y * core_glyph_y_offset + core_glyph_height // 2
        buffer_page_glyph_y_location = (
            buffer_page_glyph_y_location - (l1_size - buffer_page.page_address) / (l1_size) * core_glyph_height
        )
        buffer_page_glyph_x_location = buffer_page.core_x * core_glyph_x_offset

        buffer_page_glyph_height = buffer_page.page_size / l1_size * core_glyph_height

        buffer_pages_glyph_y_location.append(buffer_page_glyph_y_location)
        buffer_pages_glyph_x_location.append(buffer_page_glyph_x_location)
        buffer_pages_height.append(buffer_page_glyph_height)
        buffer_pages_width.append(core_glyph_width)
        buffer_pages_color.append(
            COLORS[
                BUFFER_TO_COLOR_INDEX[(buffer_page.device_id, buffer_page.address, buffer_page.buffer_type)]
                % len(COLORS)
            ]
        )
        num_buffer_pages += 1
    if num_buffer_pages == 0:
        return (
            "",
            "Detailed L1 Buffer Report is not Available! Set  TTNN_CONFIG_OVERRIDES='{\"enable_detailed_buffer_report\": true}' in your environment",
        )

    buffer_pages_glyph_x_location = np.asarray(buffer_pages_glyph_x_location)
    buffer_pages_glyph_y_location = np.asarray(buffer_pages_glyph_y_location)
    buffer_pages_height = np.asarray(buffer_pages_height)
    buffer_pages_width = np.asarray(buffer_pages_width)
    buffer_pages_data_source = ColumnDataSource(
        dict(
            glyph_y_location=buffer_pages_glyph_y_location,
            glyph_x_location=buffer_pages_glyph_x_location,
            glyph_height=buffer_pages_height,
            glyph_width=buffer_pages_width,
            color=buffer_pages_color,
        )
    )

    plot = Plot(title=None, width=800, height=800, min_border=0, toolbar_location="below")

    plot.y_range = Range1d(-100, 1200)
    plot.x_range = Range1d(-10, 250)

    xaxis = LinearAxis()
    plot.add_layout(xaxis, "below")

    yaxis = LinearAxis()
    plot.add_layout(yaxis, "left")

    plot.yaxis.axis_label = "Core Y"
    plot.xaxis.axis_label = "Core X"

    plot.yaxis.ticker.desired_num_ticks = 1
    plot.yaxis.formatter = CustomJSTickFormatter(
        code=f"""
        return "";
    """
    )
    plot.xaxis.ticker.desired_num_ticks = 1
    plot.xaxis.formatter = CustomJSTickFormatter(
        code=f"""
        return "";
    """
    )

    core_glyph = Rect(
        y="glyph_y_location",
        x="glyph_x_location",
        height="glyph_height",
        width="glyph_width",
        line_color="black",
        fill_color="white",
    )
    core_renderer = plot.add_glyph(cores_data_source, core_glyph)

    buffer_page_glyph = Rect(
        y="glyph_y_location",
        x="glyph_x_location",
        height="glyph_height",
        width="glyph_width",
        line_color=None,
        fill_color="color",
    )
    plot.add_glyph(buffer_pages_data_source, buffer_page_glyph)

    plot.add_tools(
        WheelZoomTool(),
        PanTool(),
        ResetTool(),
        ZoomInTool(),
        ZoomOutTool(),
        HoverTool(
            renderers=[core_renderer],
            tooltips=[("Core", "(@core_y, @core_x)")],
        ),
    )
    return components(plot)


@app.route("/operation_buffer_report/<operation_id>")
def operation_buffer_report(operation_id):
    operation, previous_operation, next_operation = ttnn.database.query_operation_by_id_together_with_previous_and_next(
        operation_id=operation_id
    )

    current_summarized_l1_report_script, current_summarized_l1_report_div = create_summarized_l1_buffer_plot(
        operation_id
    )
    current_detailed_l1_report_script, current_detailed_l1_report_div = create_detailed_l1_buffer_plot(operation_id)

    if previous_operation is not None:
        previous_summarized_l1_report_script, previous_summarized_l1_report_div = create_summarized_l1_buffer_plot(
            previous_operation.operation_id
        )
    else:
        previous_summarized_l1_report_script, previous_summarized_l1_report_div = "", ""

    def get_tensor_color(tensor):
        if (tensor.device_id, tensor.address, tensor.buffer_type) not in BUFFER_TO_COLOR_INDEX:
            return "white"
        color_index = BUFFER_TO_COLOR_INDEX[(tensor.device_id, tensor.address, tensor.buffer_type)] % len(COLORS)
        return COLORS[color_index]

    input_tensor_records = ttnn.database.query_input_tensors(operation_id=operation_id)
    input_tensor_records = sorted(input_tensor_records, key=lambda tensor: tensor.input_index)
    input_tensors = [ttnn.database.query_tensor_by_id(tensor_id=tensor.tensor_id) for tensor in input_tensor_records]

    output_tensor_records = ttnn.database.query_output_tensors(operation_id=operation_id)
    output_tensor_records = sorted(output_tensor_records, key=lambda tensor: tensor.output_index)
    output_tensors = [ttnn.database.query_tensor_by_id(tensor_id=tensor.tensor_id) for tensor in output_tensor_records]

    stack_trace = ttnn.database.query_stack_trace(operation_id=operation_id)
    stack_trace = shorten_stack_trace(stack_trace)

    return render_template(
        "operation_buffer_report.html",
        operation=operation,
        previous_operation=previous_operation,
        next_operation=next_operation,
        current_summarized_l1_report_script=current_summarized_l1_report_script,
        current_summarized_l1_report_div=current_summarized_l1_report_div,
        previous_summarized_l1_report_script=previous_summarized_l1_report_script,
        previous_summarized_l1_report_div=previous_summarized_l1_report_div,
        current_detailed_l1_report_script=current_detailed_l1_report_script,
        current_detailed_l1_report_div=current_detailed_l1_report_div,
        input_tensors=input_tensors,
        output_tensors=output_tensors,
        get_tensor_color=get_tensor_color,
        stack_trace=stack_trace,
    )


@app.route("/operation_graph_report/<operation_id>")
def operation_graph_report(operation_id):
    operation, previous_operation, next_operation = ttnn.database.query_operation_by_id_together_with_previous_and_next(
        operation_id=operation_id
    )

    # graph = ttnn.database.load_graph(operation_id)

    # import graphviz

    # graphviz_graph = graphviz.Digraph()
    # for node in graph:
    #     attributes = graph.nodes[node]
    #     print(attributes)
    #     node_name = attributes["name"]
    #     graphviz_graph.node(name=f"{node}", label=node_name)
    #     for child in graph[node]:
    #         graphviz_graph.edge(f"{node}", f"{child}")

    # return graphviz_graph.pipe(format="svg").decode("utf-8")
    svg_file = ttnn.CONFIG.reports_path / "graphs" / f"{operation_id}.svg"
    if not svg_file.exists():
        return "Graph not found! Was TTNN_CONFIG_OVERRIDES='{\"enable_graph_report\": true}' set?"
    with open(svg_file) as f:
        graph_svg = f.read()
    return render_template(
        "operation_graph_report.html",
        operation=operation,
        previous_operation=previous_operation,
        next_operation=next_operation,
        graph_svg=graph_svg,
    )


@app.route("/operation_tensor_report/<operation_id>")
def operation_tensor_report(operation_id):
    operation, previous_operation, next_operation = ttnn.database.query_operation_by_id_together_with_previous_and_next(
        operation_id=operation_id
    )

    operation_arguments = list(ttnn.database.query_operation_arguments(operation_id=operation_id))

    input_tensor_records = ttnn.database.query_input_tensors(operation_id=operation_id)
    input_tensor_records = sorted(input_tensor_records, key=lambda tensor: tensor.input_index)
    input_tensors = [
        (
            ttnn.database.query_tensor_by_id(tensor_id=tensor.tensor_id),
            ttnn.database.load_tensor_by_id(tensor.tensor_id),
        )
        for tensor in input_tensor_records
    ]

    output_tensor_records = ttnn.database.query_output_tensors(operation_id=operation_id)
    output_tensor_records = sorted(output_tensor_records, key=lambda tensor: tensor.output_index)
    output_tensors = [
        (
            ttnn.database.query_tensor_by_id(tensor_id=tensor.tensor_id),
            ttnn.database.load_tensor_by_id(tensor.tensor_id),
        )
        for tensor in output_tensor_records
    ]

    def load_golden_tensors(table_name):
        golden_tensors = {}
        for output_tensor_record in output_tensor_records:
            tensor_comparison_record = ttnn.database.query_tensor_comparison_record(
                table_name, tensor_id=output_tensor_record.tensor_id
            )

            if tensor_comparison_record is None:
                continue
            tensor_record = ttnn.database.query_tensor_by_id(tensor_id=tensor_comparison_record.golden_tensor_id)
            golden_tensor = ttnn.database.load_tensor_by_id(tensor_id=tensor_comparison_record.golden_tensor_id)
            golden_tensors[output_tensor_record.output_index] = tensor_record, tensor_comparison_record, golden_tensor
        return golden_tensors

    local_golden_tensors = load_golden_tensors("local_tensor_comparison_records")
    global_golden_tensors = load_golden_tensors("global_tensor_comparison_records")

    def display_tensor_comparison_record(record):
        percentage = tensor_comparison_record_to_percentage(record)
        bgcolor = red_to_green_spectrum(percentage)
        return f"""
            <td bgcolor="{bgcolor}">
                Desired PCC = {record.desired_pcc}<br>Actual PCC = {record.actual_pcc}
            </td>
        """

    def plot_tensor(tensor):
        import torch

        if tensor is None:
            return "", ""

        if isinstance(tensor, ttnn.Tensor):
            tensor = ttnn.to_torch(tensor)

        if tensor.dtype == torch.bfloat16:
            tensor = tensor.float()

        tensor = tensor.numpy()

        if tensor.ndim == 1:
            tensor = tensor.reshape(1, -1)
        elif tensor.ndim == 2:
            tensor = tensor
        elif tensor.ndim == 3:
            tensor = tensor[0]
        elif tensor.ndim == 4:
            tensor = tensor[0, 0]
        else:
            raise ValueError(f"Unsupported tensor shape {tensor.shape}")

        tensor = tensor[:1024, :1024]

        plot = figure(tooltips=[("x", "$x"), ("y", "$y"), ("value", "@image")], height=400, width=400)
        plot.x_range.range_padding = plot.y_range.range_padding = 0

        # must give a vector of image data for image parameter
        plot.image(image=[tensor], x=0, y=0, dw=tensor.shape[-1], dh=tensor.shape[-2], palette="Viridis10")

        return components(plot)

    def get_tensor_statistics(tensor):
        if tensor is None:
            return ""

        if isinstance(tensor, ttnn.Tensor):
            tensor = ttnn.to_torch(tensor)

        tensor = tensor.double()

        statistics = {
            "Min": tensor.min().item(),
            "Max": tensor.max().item(),
            "Mean": tensor.mean().item(),
            "Std": tensor.std().item(),
            "Var": tensor.var().item(),
        }

        return pd.DataFrame(statistics, index=[0]).to_html(index=False, justify="center")

    return render_template(
        "operation_tensor_report.html",
        operation=operation,
        previous_operation=previous_operation,
        next_operation=next_operation,
        operation_arguments=operation_arguments,
        input_tensors=input_tensors,
        output_tensors=output_tensors,
        local_golden_tensors=local_golden_tensors,
        global_golden_tensors=global_golden_tensors,
        display_tensor_comparison_record=display_tensor_comparison_record,
        plot_tensor=plot_tensor,
        get_tensor_file_name_by_id=ttnn.database.get_tensor_file_name_by_id,
        get_tensor_statistics=get_tensor_statistics,
    )


@app.route("/operation_stack_trace/<operation_id>")
def operation_stack_trace(operation_id):
    operation, previous_operation, next_operation = ttnn.database.query_operation_by_id_together_with_previous_and_next(
        operation_id=operation_id
    )
    stack_trace = ttnn.database.query_stack_trace(operation_id=operation_id)
    return render_template(
        "operation_stack_trace.html",
        operation=operation,
        previous_operation=previous_operation,
        next_operation=next_operation,
        stack_trace=stack_trace,
    )


if __name__ == "__main__":
    app.run(debug=True)
