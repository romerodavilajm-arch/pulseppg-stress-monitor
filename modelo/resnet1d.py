"""Red ResNet1D del encoder de Pulse-PPG, copiada sin cambios de arquitectura.

Origen: https://github.com/maxxu05/pulseppg (pulseppg/nets/ResNet1D/ResNet1D_Net.py),
licencia MIT, Copyright (c) 2025 maxxu05; a su vez basada en el ResNet1D de
Shenda Hong (2019). Solo se quitaron imports sin uso (matplotlib, tqdm,
sklearn) y los print de depuración, para no instalarlos en la imagen. Los
nombres de las capas deben quedar iguales: son las llaves del state_dict de
los pesos publicados.

Entrada (n, 1, largo) → salida (n, 512) con los parámetros de PULSEPPG_PARAMS.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

# Configuración del modelo publicado (experiments/configs/PulsePPG_expconfigs.py).
PULSEPPG_PARAMS = {
    "in_channels": 1,
    "base_filters": 128,
    "kernel_size": 11,
    "stride": 2,
    "groups": 1,
    "n_block": 12,
    "finalpool": "max",
}


class MyConv1dPadSame(nn.Module):
    """Conv1d con padding SAME."""

    def __init__(self, in_channels, out_channels, kernel_size, stride, groups=1):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = kernel_size
        self.stride = stride
        self.groups = groups
        self.conv = torch.nn.Conv1d(
            in_channels=self.in_channels,
            out_channels=self.out_channels,
            kernel_size=self.kernel_size,
            stride=self.stride,
            groups=self.groups,
        )

    def forward(self, x):
        in_dim = x.shape[-1]
        out_dim = (in_dim + self.stride - 1) // self.stride
        p = max(0, (out_dim - 1) * self.stride + self.kernel_size - in_dim)
        pad_left = p // 2
        pad_right = p - pad_left
        x = F.pad(x, (pad_left, pad_right), "constant", 0)
        return self.conv(x)


class MyMaxPool1dPadSame(nn.Module):
    """MaxPool1d con padding SAME."""

    def __init__(self, kernel_size):
        super().__init__()
        self.kernel_size = kernel_size
        self.stride = 1
        self.max_pool = torch.nn.MaxPool1d(kernel_size=self.kernel_size)

    def forward(self, x):
        in_dim = x.shape[-1]
        out_dim = (in_dim + self.stride - 1) // self.stride
        p = max(0, (out_dim - 1) * self.stride + self.kernel_size - in_dim)
        pad_left = p // 2
        pad_right = p - pad_left
        x = F.pad(x, (pad_left, pad_right), "constant", 0)
        return self.max_pool(x)


class BasicBlock(nn.Module):
    """Bloque residual básico."""

    def __init__(self, in_channels, out_channels, kernel_size, stride, groups,
                 downsample, use_bn, use_do, is_first_block=False):
        super().__init__()
        self.in_channels = in_channels
        self.kernel_size = kernel_size
        self.out_channels = out_channels
        self.groups = groups
        self.downsample = downsample
        self.stride = stride if self.downsample else 1
        self.is_first_block = is_first_block
        self.use_bn = use_bn
        self.use_do = use_do

        self.bn1 = nn.BatchNorm1d(in_channels)
        self.relu1 = nn.ReLU()
        self.do1 = nn.Dropout(p=0.5)
        self.conv1 = MyConv1dPadSame(
            in_channels=in_channels, out_channels=out_channels,
            kernel_size=kernel_size, stride=self.stride, groups=self.groups,
        )

        self.bn2 = nn.BatchNorm1d(out_channels)
        self.relu2 = nn.ReLU()
        self.do2 = nn.Dropout(p=0.5)
        self.conv2 = MyConv1dPadSame(
            in_channels=out_channels, out_channels=out_channels,
            kernel_size=kernel_size, stride=1, groups=self.groups,
        )

        self.max_pool = MyMaxPool1dPadSame(kernel_size=self.stride)

    def forward(self, x):
        identity = x

        out = x
        if not self.is_first_block:
            if self.use_bn:
                out = self.bn1(out)
            out = self.relu1(out)
            if self.use_do:
                out = self.do1(out)
        out = self.conv1(out)

        if self.use_bn:
            out = self.bn2(out)
        out = self.relu2(out)
        if self.use_do:
            out = self.do2(out)
        out = self.conv2(out)

        if self.downsample:
            identity = self.max_pool(identity)

        if self.out_channels != self.in_channels:
            identity = identity.transpose(-1, -2)
            ch1 = (self.out_channels - self.in_channels) // 2
            ch2 = self.out_channels - self.in_channels - ch1
            identity = F.pad(identity, (ch1, ch2), "constant", 0)
            identity = identity.transpose(-1, -2)

        out += identity
        return out


class Net(nn.Module):
    """ResNet1D: (n, canales, largo) → (n, filtros finales) con finalpool."""

    def __init__(self, in_channels, base_filters, kernel_size, stride, groups, n_block,
                 finalpool=None, downsample_gap=2, increasefilter_gap=4,
                 use_bn=True, use_do=True):
        super().__init__()
        self.n_block = n_block
        self.kernel_size = kernel_size
        self.stride = stride
        self.groups = groups
        self.use_bn = use_bn
        self.use_do = use_do
        self.downsample_gap = downsample_gap
        self.increasefilter_gap = increasefilter_gap

        self.first_block_conv = MyConv1dPadSame(
            in_channels=in_channels, out_channels=base_filters,
            kernel_size=self.kernel_size, stride=1,
        )
        self.first_block_bn = nn.BatchNorm1d(base_filters)
        self.first_block_relu = nn.ReLU()
        out_channels = base_filters

        self.basicblock_list = nn.ModuleList()
        for i_block in range(self.n_block):
            is_first_block = i_block == 0
            downsample = i_block % self.downsample_gap == 1
            if is_first_block:
                in_channels = base_filters
                out_channels = in_channels
            else:
                in_channels = int(base_filters * 2 ** ((i_block - 1) // self.increasefilter_gap))
                if (i_block % self.increasefilter_gap == 0) and (i_block != 0):
                    out_channels = in_channels * 2
                else:
                    out_channels = in_channels

            self.basicblock_list.append(BasicBlock(
                in_channels=in_channels, out_channels=out_channels,
                kernel_size=self.kernel_size, stride=self.stride, groups=self.groups,
                downsample=downsample, use_bn=self.use_bn, use_do=self.use_do,
                is_first_block=is_first_block,
            ))

        # Igual que el original: in_channels aquí ya es el del último bloque,
        # pero InstanceNorm1d sin affine no tiene pesos, así que no importa.
        self.instnorm = nn.InstanceNorm1d(in_channels)
        self.finalpool = finalpool
        self.out_dim = out_channels

    def forward(self, x):
        out = self.instnorm(x)
        out = self.first_block_conv(out)
        if self.use_bn:
            out = self.first_block_bn(out)
        out = self.first_block_relu(out)

        for block in self.basicblock_list:
            out = block(out)

        if self.finalpool == "avg":
            out = torch.mean(out, dim=-1)
        elif self.finalpool == "max":
            out = torch.max(out, dim=-1)[0]
        elif self.finalpool:
            raise ValueError(f"finalpool inválido: {self.finalpool}")
        return out
