#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import onnx
import tensorrt as trt


def inspect_onnx(path: Path) -> dict:
    model = onnx.load(str(path))
    onnx.checker.check_model(model)

    def info(value):
        dims = [dim.dim_param or dim.dim_value or "dynamic" for dim in value.type.tensor_type.shape.dim]
        return {"name": value.name, "shape": dims}

    return {
        "inputs": [info(value) for value in model.graph.input],
        "outputs": [info(value) for value in model.graph.output],
    }


def build_engine(onnx_path: Path, engine_path: Path) -> dict:
    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    parser = trt.OnnxParser(network, logger)
    if not parser.parse(onnx_path.read_bytes()):
        errors = [str(parser.get_error(index)) for index in range(parser.num_errors)]
        raise RuntimeError("ONNX parsing failed:\n" + "\n".join(errors))

    input_tensor = network.get_input(0)
    if input_tensor.name != "images":
        raise RuntimeError(f"Expected input 'images', found {input_tensor.name!r}")

    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 2 << 30)
    if not builder.platform_has_fast_fp16:
        raise RuntimeError("This GPU does not support fast FP16")
    config.set_flag(trt.BuilderFlag.FP16)

    profile = builder.create_optimization_profile()
    profile.set_shape(
        input_tensor.name,
        min=(1, 3, 320, 320),
        opt=(4, 3, 640, 640),
        max=(8, 3, 640, 640),
    )
    config.add_optimization_profile(profile)

    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        raise RuntimeError("TensorRT engine build failed")
    engine_path.write_bytes(serialized)

    runtime = trt.Runtime(logger)
    engine = runtime.deserialize_cuda_engine(serialized)
    if engine is None:
        raise RuntimeError("TensorRT engine validation failed")
    tensors = []
    for index in range(engine.num_io_tensors):
        name = engine.get_tensor_name(index)
        tensors.append({
            "name": name,
            "mode": str(engine.get_tensor_mode(name)),
            "shape": list(engine.get_tensor_shape(name)),
            "dtype": str(engine.get_tensor_dtype(name)),
        })
    return {
        "precision": "FP16",
        "profiles": {"images": {"min": [1, 3, 320, 320], "opt": [4, 3, 640, 640], "max": [8, 3, 640, 640]}},
        "inputs_outputs": tensors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()
    metadata = {"onnx": inspect_onnx(args.onnx), "tensorrt": build_engine(args.onnx, args.engine)}
    args.metadata.write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
