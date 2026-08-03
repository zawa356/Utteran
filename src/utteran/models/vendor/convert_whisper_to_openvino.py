"""whisper.cpp v1.9.1 converter with OpenVINO 2025+ compatibility fixes."""
# mypy: ignore-errors

import argparse
import os
import shutil

import torch
from openvino import serialize
from openvino.frontend import FrontEndManager
from whisper import load_model


def convert_encoder(hparams, encoder, model_name):
    encoder.eval()
    mel = torch.zeros((1, hparams.n_mels, 3000))
    onnx_folder = os.path.join(os.path.dirname(__file__), "onnx_encoder")
    os.makedirs(onnx_folder, exist_ok=True)
    onnx_path = os.path.join(onnx_folder, "whisper_encoder.onnx")
    torch.onnx.export(
        encoder,
        mel,
        onnx_path,
        input_names=["mel"],
        output_names=["output_features"],
    )
    frontend = FrontEndManager().load_by_framework("onnx")
    ov_model = frontend.convert(frontend.load(onnx_path))
    serialize(
        ov_model,
        xml_path=os.path.join(
            os.path.dirname(__file__), f"ggml-{model_name}-encoder-openvino.xml"
        ),
    )
    shutil.rmtree(onnx_folder, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    arguments = parser.parse_args()
    model = load_model(arguments.model).cpu()
    convert_encoder(model.dims, model.encoder, arguments.model)
