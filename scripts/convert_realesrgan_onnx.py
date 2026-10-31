"""Converts the official Real-ESRGAN x4plus weights (BSD-3, xinntao/Real-ESRGAN v0.1.0) to ONNX.

RRDBNet as defined in basicsr (num_feat=64, num_block=23, num_grow_ch=32, scale=4). Weights are
loaded with weights_only=True (tensor-only unpickler: the file cannot execute code). The export
uses dynamic height/width; the ONNX output is compared with PyTorch's on a test image.

Run once, in a separate environment (PyTorch is not an application dependency):
    python -m venv C:	mp\esr && C:	mp\esr\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu
    C:	mp\esr\Scripts\pip install onnx onnxruntime numpy
    C:	mp\esr\Scripts\python -I scripts\convert_realesrgan_onnx.py RealESRGAN_x4plus.pth tools\modelsealesrgan_x4plus.onnx
See docs/upscaler.md for the source, hashes and validation.
"""
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class ResidualDenseBlock(nn.Module):
    def __init__(self, nf=64, gc=32):
        super().__init__()
        self.conv1 = nn.Conv2d(nf, gc, 3, 1, 1)
        self.conv2 = nn.Conv2d(nf + gc, gc, 3, 1, 1)
        self.conv3 = nn.Conv2d(nf + 2 * gc, gc, 3, 1, 1)
        self.conv4 = nn.Conv2d(nf + 3 * gc, gc, 3, 1, 1)
        self.conv5 = nn.Conv2d(nf + 4 * gc, nf, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class RRDB(nn.Module):
    def __init__(self, nf, gc=32):
        super().__init__()
        self.rdb1 = ResidualDenseBlock(nf, gc)
        self.rdb2 = ResidualDenseBlock(nf, gc)
        self.rdb3 = ResidualDenseBlock(nf, gc)

    def forward(self, x):
        return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x


class RRDBNet(nn.Module):
    def __init__(self, in_ch=3, out_ch=3, nf=64, nb=23, gc=32):
        super().__init__()
        self.conv_first = nn.Conv2d(in_ch, nf, 3, 1, 1)
        self.body = nn.Sequential(*[RRDB(nf, gc) for _ in range(nb)])
        self.conv_body = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_up1 = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
        self.conv_last = nn.Conv2d(nf, out_ch, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        feat = self.conv_first(x)
        feat = feat + self.conv_body(self.body(feat))
        feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
        feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
        return self.conv_last(self.lrelu(self.conv_hr(feat)))


def main(weights, out):
    state = torch.load(weights, map_location="cpu", weights_only=True)
    state = state.get("params_ema", state.get("params", state))
    net = RRDBNet()
    loaded = net.load_state_dict(state, strict=True)
    net.eval()
    dummy = torch.rand(1, 3, 64, 64)
    torch.onnx.export(net, dummy, out, input_names=["input"], output_names=["output"], opset_version=17,
                      dynamic_axes={"input": {2: "height", 3: "width"}, "output": {2: "height4", 3: "width4"}},
                      dynamo=False)
    import onnx
    import onnxruntime as ort
    onnx.checker.check_model(onnx.load(out))
    test = torch.rand(1, 3, 48, 80)
    with torch.no_grad():
        ref = net(test).numpy()
    got = ort.InferenceSession(out, providers=["CPUExecutionProvider"]).run(None, {"input": test.numpy()})[0]
    print("onnx output shape:", got.shape, "| max abs diff vs PyTorch:", float(np.abs(ref - got).max()))
    print("loaded weights:", loaded)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
